"""Postgres-backed queue. Crash recovery never repeats a potentially billable call."""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select, true

from cutback.core.isolated_episodes import GuardedRecordExecution
from cutback.core.observation import observation_scope
from cutback.evals.datasets import CapturePolicy, DatasetImport, digest
from cutback.evals.experiments import provider_models, run
from cutback.evals.input_groups import InputGroupingViolation
from cutback.server.content_policy import require_payloads_enabled
from cutback.server.db import (
    AuditEvent,
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
    RecommendationRecord,
)
from cutback.server.plan_artifacts import load_plan, public_config


def load_dataset(
    session, dataset, store, *, now=None, check=None, max_cases=None, max_content_bytes=None
):
    require_payloads_enabled()
    from cutback.server.scenario_service import require_current_import

    require_current_import(session, store, dataset)
    now = now or datetime.now(UTC)
    policy_row = session.get(CapturePolicyRecord, dataset.provenance["policy_id"])
    if not policy_row or policy_row.project_id != dataset.project_id or not policy_row.enabled:
        raise ValueError("CAPTURE_POLICY_REVOKED")
    policy = CapturePolicy.model_validate(policy_row.policy)
    if not policy.permits(dataset.provenance["environment"], dataset.workflow_id):
        raise ValueError("CAPTURE_POLICY_EXPIRED_OR_OUT_OF_SCOPE")
    query = select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
    if max_cases is not None:
        query = query.limit(max_cases + 1)
    items = session.scalars(query).all()
    if max_cases is not None and len(items) > max_cases:
        raise ValueError("DATASET_READ_CASE_BOUND")
    ordered = []
    expiries = []
    content_bytes = 0
    for item in items:
        if check:
            check()
        payload = session.get(PayloadSample, item.payload_id)
        if payload.project_id != dataset.project_id or payload.expires_at <= now:
            raise ValueError("PAYLOAD_EXPIRED_OR_OUT_OF_SCOPE")
        raw = store.get(dataset.project_id, payload.object_ref)
        content_bytes += len(raw)
        if max_content_bytes is not None and content_bytes > max_content_bytes:
            raise ValueError("DATASET_READ_CONTENT_BOUND")
        if check:
            check()
        case = json.loads(raw)
        expiries.append(payload.expires_at)
        if digest(case) != payload.content_hash:
            raise ValueError("PAYLOAD_INTEGRITY_FAILURE")
        from cutback.server.content_policy import policy_content

        policy_content(case, policy, preserve_semantics=True)
        ordered.append(
            (
                payload.metadata_safe["ordinal"],
                {
                    "case": case,
                    "split": item.split,
                    "group_hash": item.group_hash,
                    "lineage_group_id": str(item.lineage_group_id)
                    if item.lineage_group_id
                    else None,
                },
            )
        )
    ordered.sort(key=lambda pair: pair[0])
    fields = set(DatasetImport.model_fields) - {"cases"}
    original = {k: v for k, v in dataset.provenance.items() if k in fields}
    original["cases"] = [item["case"] for _, item in ordered]
    content_hash = digest(DatasetImport.model_validate(original).model_dump(mode="json"))
    expected_hash = content_hash
    if dataset.provenance.get("hash_scheme") == "cutback.dataset.lineage.v1":
        from cutback.server.dataset_lineage import dataset_version_hash

        assignment_hash = digest(
            [
                [ordinal, row["split"], row["group_hash"], row["lineage_group_id"]]
                for ordinal, row in ordered
            ]
        )
        if (
            content_hash != dataset.provenance["content_hash"]
            or assignment_hash != dataset.provenance["assignment_hash"]
        ):
            raise ValueError("DATASET_LINEAGE_ASSIGNMENT_INTEGRITY_FAILURE")
        expected_hash = dataset_version_hash(dataset.provenance, content_hash, assignment_hash)
    if expected_hash != dataset.immutable_hash:
        raise ValueError("DATASET_INTEGRITY_FAILURE")
    return {
        "immutable_hash": dataset.immutable_hash,
        **(
            {"security_suite": dataset.provenance["security_suite"]}
            if dataset.provenance.get("security_suite")
            else {}
        ),
        "items": [item for _, item in ordered],
        "expires_at": min(expiries).isoformat(),
    }, policy


def run_one(
    sessions,
    store,
    *,
    executor=None,
    execution_enabled=False,
    only_run_id=None,
    tool_client=None,
    guardrail_client=None,
):
    only_run_id = UUID(str(only_run_id)) if only_run_id is not None else None
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    owned_executor = executor is None
    owned_tool_client = False
    owned_guardrail_client = False
    if owned_executor:
        from cutback.evals.executors import ConfiguredExecutor

        try:
            executor = ConfiguredExecutor()
        except (ValueError, ImportError):
            return {"status": "NO_EXPERIMENT_API_KEY_OR_SUPPORTED_EXTRA", "provider_calls": 0}
    try:
        from cutback.server.execution_ledger import recover_execution_leases

        recover_execution_leases(sessions, only_run_id=only_run_id)
        with sessions.begin() as session:
            now = datetime.now(UTC)
            # Expired leases become failed, never requeued: a lost HTTP response may be billable.
            expired = session.scalars(
                select(JobRecord)
                .where(JobRecord.status == "RUNNING", JobRecord.lease_until < now)
                .where(JobRecord.run_id == only_run_id if only_run_id else true())
                .with_for_update(skip_locked=True)
                .limit(100)
            ).all()
            for lost in expired:
                lost.status = "FAILED"
                record = session.get(ExperimentRunRecord, lost.run_id)
                if record.status == "RUNNING":
                    record.status = "FAILED"
                    record.results = {"code": "WORKER_LEASE_EXPIRED_BILLING_UNCERTAIN"}
                session.add(
                    AuditEvent(
                        project_id=lost.project_id,
                        actor="experiment_worker",
                        action="experiment_lease_expired",
                        details={"run_id": str(lost.run_id)},
                    )
                )
            job = session.scalar(
                select(JobRecord)
                .where(
                    JobRecord.execution_kind == "evaluation",
                    or_(
                        JobRecord.status == "QUEUED",
                        and_(JobRecord.status == "BLOCKED", JobRecord.available_at <= now),
                    ),
                )
                .where(JobRecord.run_id == only_run_id if only_run_id else true())
                .order_by(JobRecord.created_at, JobRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return {"status": "IDLE"}
            record = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
            if record.status not in {"QUEUED", "BLOCKED"}:
                job.status = record.status
                return {"status": record.status}
            plan_row = session.get(ExperimentPlanRecord, record.plan_id)
            try:
                plan = load_plan(store, record.project_id, plan_row.specification)
            except Exception:
                job.status = record.status = "FAILED"
                record.results = {"code": "PLAN_ARTIFACT_EXPIRED_OR_UNAVAILABLE"}
                return {"status": "FAILED", "provider_calls": 0}
            from cutback.server.campaign_service import authorize_campaign_run

            if (
                plan.native_fixture is not None
                and getattr(executor, "synthetic_transport", False) is not True
            ):
                record.status = job.status = "BLOCKED"
                job.available_at = None
                record.results = {
                    "code": "SCRIPTED_NATIVE_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR",
                    "next_action": "use the configured fixture worker for this saved plan",
                    "provider_calls": 0,
                    "native_fixture": plan.native_fixture.public_summary(),
                }
                return {"status": "BLOCKED", "run_id": str(record.id), "provider_calls": 0}

            if (
                plan.routing_execution is not None
                and plan.routing_execution.transport == "scripted_test_only"
                and getattr(executor, "synthetic_transport", False) is not True
            ):
                record.status = job.status = "BLOCKED"
                job.available_at = None
                record.results = {
                    "code": "SCRIPTED_ROUTING_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR",
                    "next_action": "use the configured fixture worker for this saved plan",
                    "provider_calls": 0,
                }
                return {"status": "BLOCKED", "run_id": str(record.id), "provider_calls": 0}

            try:
                authorize_campaign_run(
                    session,
                    store,
                    record.id,
                    synthetic_transport=getattr(executor, "synthetic_transport", False),
                )
            except ValueError:
                job.status = record.status = "FAILED"
                record.results = {"code": "CAMPAIGN_AUTHORITY_OR_TRANSPORT_INVALID"}
                return {"status": "FAILED", "provider_calls": 0}
            if owned_executor:
                if (
                    plan.episode_execution
                    and plan.episode_execution.transport == "scripted_test_only"
                ):
                    record.status = job.status = "BLOCKED"
                    job.available_at = None
                    record.results = {
                        "code": "SCRIPTED_EPISODE_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR",
                        "next_action": "use the supplied scripted test harness; "
                        "provider-key workers cannot execute this plan",
                    }
                    return {"status": "BLOCKED", "run_id": str(record.id), "provider_calls": 0}
                providers = {
                    target.provider
                    for model in [
                        plan.baseline,
                        *plan.candidates,
                        *([plan.judge] if plan.judge else []),
                        *(
                            [plan.episode_execution.user_simulation.model]
                            if plan.episode_execution and plan.episode_execution.user_simulation
                            else []
                        ),
                    ]
                    for target in provider_models(model)
                }
                missing = (
                    ("openai" in providers and not os.getenv("CUTBACK_EXPERIMENT_OPENAI_KEY"))
                    or (
                        "anthropic" in providers
                        and not os.getenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY")
                    )
                    or ("local" in providers and not os.getenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT"))
                )
                if missing:
                    record.status = job.status = "BLOCKED"
                    job.available_at = now + timedelta(seconds=30)
                    record.results = {
                        "code": "NO_EXPERIMENT_KEY_OR_LOCAL_ENDPOINT",
                        "next_action": "configure approved worker credentials; no calls made",
                        "retry_after": job.available_at.isoformat(),
                    }
                    return {
                        "status": "BLOCKED",
                        "code": "NO_EXPERIMENT_KEY_OR_LOCAL_ENDPOINT",
                        "provider_calls": 0,
                    }
                if plan.episode_execution:
                    from cutback.evals.agent_executor import NativeAgentExecutor

                    executor.close()
                    executor = NativeAgentExecutor()
            if (
                plan.episode_execution
                and plan.episode_execution.isolated_tools
                and tool_client is None
            ):
                from cutback.server.tool_execution import configured_tool_client

                try:
                    tool_client = configured_tool_client(plan.episode_execution.isolated_tools)
                    owned_tool_client = True
                except (ValueError, KeyError):
                    record.status = job.status = "BLOCKED"
                    job.available_at = None
                    record.results = {
                        "code": "ISOLATED_TOOL_WORKER_NOT_CONFIGURED",
                        "provider_calls": 0,
                    }
                    return {"status": "BLOCKED", "provider_calls": 0}
            guarded = plan.episode_execution and isinstance(
                plan.episode_execution.isolated_tools, GuardedRecordExecution
            )
            if guarded:
                try:
                    if guardrail_client is None:
                        from cutback.server.tool_execution import configured_tool_guard_client

                        guardrail_client = configured_tool_guard_client(
                            plan.episode_execution.isolated_tools, plan.workflow
                        )
                        owned_guardrail_client = True
                    if (
                        guardrail_client.binding
                        != plan.episode_execution.isolated_tools.runtime_guardrails
                        or guardrail_client.workflow != plan.workflow
                    ):
                        raise ValueError("SCOPE")
                except (ValueError, KeyError, ImportError):
                    record.status = job.status = "BLOCKED"
                    job.available_at = None
                    record.results = {
                        "code": "GUARDED_TOOL_CHECKER_NOT_CONFIGURED",
                        "provider_calls": 0,
                    }
                    return {"status": "BLOCKED", "provider_calls": 0}
            record.status, job.status = "RUNNING", "RUNNING"
            job.available_at = None
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=plan.budget.max_wall_seconds + 60)
            run_id, job_id, project_id = record.id, job.id, record.project_id
            dataset_id = plan_row.dataset_id
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor="experiment_worker",
                    action="experiment_started",
                    details={"run_id": str(run_id)},
                )
            )
        try:
            with sessions() as session:
                from cutback.server.dataset_lineage import lock_lineage

                lock_lineage(session, project_id)
                dataset_row = session.get(DatasetRecord, dataset_id)
                source_routing = plan.routing_execution and plan.routing_execution.source
                from cutback.core.input_routing import (
                    SOURCE_DATASET_CASE_BOUND,
                    SOURCE_DATASET_CONTENT_BOUND,
                )

                dataset, policy = load_dataset(
                    session,
                    dataset_row,
                    store,
                    max_cases=SOURCE_DATASET_CASE_BOUND if source_routing else None,
                    max_content_bytes=SOURCE_DATASET_CONTENT_BOUND if source_routing else None,
                )
                if any(
                    provider not in policy.allowed_providers for provider in plan.approved_providers
                ):
                    raise ValueError("PROVIDER_POLICY_DENIED")
                provenance = dataset_row.provenance
                from cutback.server.native_fixture import require_fixture_inputs

                require_fixture_inputs(plan, dataset_row, policy)
                from cutback.server.stages import validate_stage

                validate_stage(session, store, project_id, plan, run_id=run_id)
                from cutback.server.dataset_lineage import evaluation_receipt

                evaluation_receipt(session, project_id, dataset_id, plan, run_id)
                from cutback.server.prompt_experiments import recheck_bindings

                recheck_bindings(session, store, project_id, plan_row, plan)
                from cutback.server.criteria_service import validate_binding as validate_criteria

                validate_criteria(session, store, project_id, plan)
                from cutback.server.scenario_service import require_current_import

                require_current_import(session, store, session.get(DatasetRecord, dataset_id))
                from cutback.server.routing_service import validate_binding as validate_routing

                validate_routing(session, store, project_id, plan, dataset_id=dataset_id)

            def authorize_execution(session):
                require_payloads_enabled()
                if plan.native_fixture is not None:
                    from cutback.server.evaluation_api import active_policy

                    fixture_dataset = session.get(DatasetRecord, dataset_id, populate_existing=True)
                    if fixture_dataset is None:
                        raise ValueError("NATIVE_FIXTURE_REQUIRES_SYNTHETIC_DATA_AND_RIGHTS")
                    _, fixture_policy = active_policy(
                        session, project_id, UUID(fixture_dataset.provenance["policy_id"])
                    )
                    require_fixture_inputs(plan, fixture_dataset, fixture_policy)
                from cutback.server.scenario_service import require_current_import

                require_current_import(session, store, session.get(DatasetRecord, dataset_id))
                authorize_campaign_run(
                    session,
                    store,
                    run_id,
                    synthetic_transport=getattr(executor, "synthetic_transport", False),
                )
                if guarded:
                    from fastapi import HTTPException

                    from cutback.server.guardrail_controls import current_authority

                    binding = plan.episode_execution.isolated_tools.runtime_guardrails
                    try:
                        current_authority(session, project_id, binding.policy_id, binding)
                    except HTTPException:
                        raise ValueError("GUARDED_TOOL_CURRENT_APPROVAL_REQUIRED") from None
                from cutback.server.principals import principal_role

                active_plan = session.get(ExperimentPlanRecord, plan_row.id, populate_existing=True)
                if not active_plan or principal_role(
                    session, project_id, active_plan.approved_by or ""
                ) not in {"admin", "project_admin"}:
                    raise ValueError("EXECUTION_APPROVING_PRINCIPAL_NO_LONGER_AUTHORIZED")
                if datetime.now(UTC) >= min(
                    datetime.fromisoformat(dataset["expires_at"]),
                    datetime.fromisoformat(plan_row.specification["expires_at"]),
                ):
                    raise ValueError("EXPERIMENT_DATA_OR_PLAN_EXPIRED")
                # Recheck revocation/expiry between calls, not only at run startup.
                from cutback.server.prompt_experiments import recheck_bindings

                recheck_bindings(session, store, project_id, plan_row, plan)
                validate_criteria(session, store, project_id, plan)
                validate_routing(session, store, project_id, plan, dataset_id=dataset_id)
                row = session.get(CapturePolicyRecord, provenance["policy_id"])
                current_policy = CapturePolicy.model_validate(row.policy) if row else None
                if (
                    not row
                    or current_policy is None
                    or not row.enabled
                    or row.project_id != project_id
                    or not current_policy.permits(provenance["environment"], plan.workflow)
                    or any(
                        provider not in current_policy.allowed_providers
                        for provider in plan.approved_providers
                    )
                    or set(current_policy.never_capture_fields) - set(policy.never_capture_fields)
                ):
                    raise ValueError("CAPTURE_POLICY_REVOKED_OR_EXPIRED")

            from cutback.server.execution_ledger import ExecutionLedger

            with sessions() as session:
                environment_id = session.scalar(
                    select(Environment.id).where(
                        Environment.project_id == project_id,
                        Environment.name == provenance["environment"],
                    )
                )
            if environment_id is None:
                raise ValueError("EXECUTION_ENVIRONMENT_NOT_REGISTERED")
            call_ledger = ExecutionLedger(
                sessions,
                run_id,
                job_id,
                plan,
                environment_id,
                authorize=authorize_execution,
            )

            from cutback.server.case_evidence import recorder

            def check_authority():
                with sessions() as session:
                    from cutback.server.dataset_lineage import lock_lineage

                    lock_lineage(session, project_id)
                    authorize_execution(session)
                    active_run = session.get(ExperimentRunRecord, run_id)
                    active_job = session.get(JobRecord, job_id)
                    if (
                        active_run.status != "RUNNING"
                        or active_job.status != "RUNNING"
                        or not active_job.lease_until
                        or active_job.lease_until <= datetime.now(UTC)
                    ):
                        raise ValueError("EXECUTION_CANCELLED_OR_LEASE_EXPIRED")

            tools_factory = None
            if plan.episode_execution and plan.episode_execution.isolated_tools:
                from cutback.server.guardrail_execution import SyncDurableGuardrailChecker
                from cutback.server.tool_execution import environment_factory

                tools_factory = environment_factory(
                    call_ledger,
                    tool_client,
                    check_authority,
                    content_expires_at=min(
                        datetime.fromisoformat(dataset["expires_at"]),
                        datetime.fromisoformat(plan_row.specification["expires_at"]),
                        datetime.now(UTC) + timedelta(seconds=plan.budget.max_wall_seconds),
                    ),
                    guard_checker=SyncDurableGuardrailChecker(sessions, guardrail_client)
                    if guarded
                    else None,
                )
            from cutback.server.native_cache import authorized_experiment_cache

            with (
                authorized_experiment_cache(
                    executor,
                    plan,
                    sessions,
                    store,
                    project_id=project_id,
                    policy_id=UUID(provenance["policy_id"]),
                    environment=provenance["environment"],
                    actor=plan_row.approved_by,
                    run_id=run_id,
                    job_id=job_id,
                    authorize=authorize_execution,
                    inputs=[
                        item["case"]["input"]
                        for item in dataset["items"]
                        if item["split"] == plan.split
                    ][: plan.sample_limit],
                ),
                observation_scope("evaluation", run_id=run_id),
            ):
                results = run(
                    plan,
                    dataset,
                    executor,
                    approval_hash=plan_row.specification["plan_hash"],
                    independent_labels=provenance["independent_labels"],
                    representative=provenance["representative"]
                    and provenance["source_type"] != "synthetic_fixture",
                    reserve_call=call_ledger.reserve,
                    record_call=call_ledger.record,
                    episode_lifecycle=call_ledger,
                    check_authority=check_authority,
                    environment_factory=tools_factory,
                    record_case=recorder(
                        sessions, store, project_id, run_id, provenance["policy_id"]
                    )
                    if plan.retain_case_outputs
                    else None,
                )
            results["case_outputs_retained"] = plan.retain_case_outputs
        except InputGroupingViolation as exc:
            results = {
                "status": "FAILED",
                "code": str(exc),
                "recommendations": [],
                "requests": 0,
            }
        except Exception:
            results = {
                "status": "FAILED",
                "code": "DATASET_POLICY_OR_EXECUTION_FAILED",
                "recommendations": [],
            }
        with sessions.begin() as session:
            from cutback.server.dataset_lineage import (
                LineageViolation,
                apply_lineage_result,
                evaluation_receipt,
                lock_lineage,
            )

            lock_lineage(session, project_id)
            record = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id)
            results = public_config(
                results, redact_content_hashes=bool(plan.prompt_bindings or plan.content_nonce)
            )
            if record.call_ledger_version == 1:
                # The database may have committed a reservation whose acknowledgement
                # was lost. In-memory callback counts cannot prove durable coverage.
                results.update(
                    requests=record.requests_used,
                    known_cost_requests=record.known_cost_requests,
                    known_spend_subtotal_usd=str(record.spent_usd),
                    reserved_usd=str(record.reserved_usd),
                )
                results["billing_complete"] = bool(
                    results.get("billing_complete")
                    and record.known_cost_requests == record.requests_used
                )
                if not results["billing_complete"]:
                    results["spent_modeled_usd"] = None
                    for recommendation in results.get("recommendations", []):
                        recommendation.update(
                            status="NEEDS_VALIDATION",
                            quality_gate_passed=False,
                            statistical_gate_passed=False,
                            decision_reason="durable_call_accounting_incomplete",
                        )
            if (
                record.status != "RUNNING"
                or job.status != "RUNNING"
                or not job.lease_until
                or job.lease_until <= datetime.now(UTC)
            ):
                # A late vendor response may repair call costs, never revive a task.
                if record.status == "RUNNING":
                    record.status = "FAILED"
                    record.results = {"code": "WORKER_LEASE_EXPIRED_BILLING_UNCERTAIN"}
                job.status = record.status
                return {"status": record.status, "run_id": str(run_id), "results": record.results}
            if results.get("status") == "COMPLETED":
                try:
                    from cutback.server.prompt_experiments import recheck_bindings

                    recheck_bindings(session, store, project_id, plan_row, plan)
                    authorize_execution(session)
                except Exception:
                    results.update(
                        status="FAILED",
                        code="PROMPT_CONTENT_AUTHORIZATION_CHANGED",
                        recommendations=[],
                    )
            if results.get("status") == "COMPLETED":
                try:
                    lineage = evaluation_receipt(session, project_id, dataset_id, plan, run_id)
                except LineageViolation as exc:
                    lineage = {
                        "contract": "cutback.lineage.v1",
                        "complete": False,
                        "fresh_test_claim": False,
                        "invalidated_after_execution": True,
                        "code": str(exc),
                    }
                apply_lineage_result(results, lineage, plan.split)
                from cutback.server.criteria_service import receipt as criteria_receipt

                criteria_evidence = criteria_receipt(session, project_id, plan)
                results.update(criteria_evidence)
                from cutback.server.routing_service import receipt as routing_receipt

                routing_evidence = routing_receipt(session, store, project_id, plan)
                results.update(routing_evidence)
                if routing_evidence.get("routing_source_current") is False:
                    results.update(
                        status="FAILED",
                        code="CURRENT_ROUTING_EVIDENCE_UNAVAILABLE",
                        recommendations=[],
                    )
                for recommendation in results.get("recommendations", []):
                    recommendation.update(criteria_evidence)
                    recommendation.update(routing_evidence)
                from cutback.evals.search import sample_frontier

                results["search_frontier"] = sample_frontier(results)
            record.results = results
            if record.status == "RUNNING":
                record.status = results["status"]
            job.status = record.status
            if record.call_ledger_version is None:
                record.spent_usd = Decimal(results.get("known_spend_subtotal_usd", "0"))
            if record.status == "COMPLETED":
                for recommendation in results["recommendations"]:
                    session.add(
                        RecommendationRecord(
                            project_id=project_id,
                            run_id=run_id,
                            status="pending",
                            evidence={
                                "experiment_name": plan.name,
                                "workflow": plan.workflow,
                                "stage": plan.stage,
                                "dataset_hash": plan.dataset_hash,
                                "plan_hash": plan_row.specification["plan_hash"],
                                **recommendation,
                            },
                        )
                    )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor="experiment_worker",
                    action="experiment_finished",
                    details={"run_id": str(run_id), "status": record.status},
                )
            )
        return {"status": record.status, "run_id": str(run_id), "results": results}
    finally:
        if owned_tool_client:
            tool_client.close()
        if owned_guardrail_client:
            guardrail_client.close()
        if owned_executor:
            executor.close()


def expire_payloads(sessions, store, *, now=None):
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        rows = session.scalars(
            select(PayloadSample)
            .where(PayloadSample.expires_at <= now)
            .with_for_update(skip_locked=True)
            .limit(1000)
        ).all()
        for row in rows:
            if not row.metadata_safe.get("deleted"):
                store.delete(row.project_id, row.object_ref)
                row.metadata_safe = {**row.metadata_safe, "deleted": True}
                session.add(
                    AuditEvent(
                        project_id=row.project_id,
                        actor="retention_worker",
                        action="expired_payload_deleted",
                        details={"payload_id": str(row.id)},
                    )
                )
        plans = session.scalars(
            select(ExperimentPlanRecord)
            .where(
                ExperimentPlanRecord.specification["expires_at"].as_string() <= now.isoformat(),
                ExperimentPlanRecord.specification["artifact_deleted"].as_boolean().is_not(True),
            )
            .with_for_update(skip_locked=True)
            .limit(1000)
        ).all()
        for plan in plans:
            store.delete(plan.project_id, plan.specification["artifact_ref"])
            plan.specification = {**plan.specification, "artifact_deleted": True}
            session.add(
                AuditEvent(
                    project_id=plan.project_id,
                    actor="retention_worker",
                    action="expired_plan_artifact_deleted",
                    details={"plan_id": str(plan.id)},
                )
            )
        return {"checked": len(rows), "plans_expired": len(plans)}


def configured_execution_enabled():
    return os.getenv("CUTBACK_EXPERIMENT_EXECUTION", "false").lower() == "true"
