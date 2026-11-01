"""One owned native generation call, durable parent reservation and no charged replay."""

import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select

from cutback.core.observation import observation_scope
from cutback.evals.datasets import digest
from cutback.evals.experiments import BudgetLedger
from cutback.evals.policy_generation import PolicyGenerationPlan
from cutback.server.campaign_content import content_transaction
from cutback.server.dataset_lineage import try_lock_lineage
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_ledger import ExecutionLedger, recover_execution_leases
from cutback.server.policy_program_content import put_content, read_content
from cutback.server.policy_program_service import (
    authorize_program,
    authorize_program_run,
    program_for_run,
)


def load_generation(store, project, metadata):
    if datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(UTC):
        raise ValueError("POLICY_GENERATION_PLAN_EXPIRED")
    try:
        blob = store.get(project, metadata["artifact_ref"])
        if len(blob) > 512000:
            raise ValueError
        raw = json.loads(blob)
        plan = PolicyGenerationPlan.model_validate(raw)
        if (
            digest(raw) != metadata["plan_hash"]
            or digest(plan.model_dump(mode="json")) != metadata["plan_hash"]
        ):
            raise ValueError
    except Exception:
        raise ValueError("POLICY_GENERATION_FROZEN_PLAN_UNAVAILABLE_OR_CHANGED") from None
    return plan


def _candidates(sessions, status, *, only_run_id=None, expired=False):
    with sessions() as reader:
        query = select(
            JobRecord.id,
            JobRecord.project_id,
            JobRecord.created_at,
            func.row_number()
            .over(partition_by=JobRecord.project_id, order_by=(JobRecord.created_at, JobRecord.id))
            .label("rank"),
        ).where(
            JobRecord.execution_kind == "source_policy_generation",
            JobRecord.status == status,
        )
        if only_run_id is not None:
            query = query.where(JobRecord.run_id == only_run_id)
        if expired:
            query = query.where(JobRecord.lease_until <= datetime.now(UTC))
        ranked = query.subquery()
        return reader.execute(
            select(ranked.c.id, ranked.c.project_id)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.created_at, ranked.c.id)
            .limit(100)
        ).all()


def run_generation_one(
    sessions, store, *, executor=None, execution_enabled=False, only_run_id=None
):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    only_run_id = UUID(str(only_run_id)) if only_run_id is not None else None
    recover_execution_leases(sessions, only_run_id=only_run_id)
    for job_id, project in _candidates(sessions, "RUNNING", only_run_id=only_run_id, expired=True):
        with sessions.begin() as session:
            if not try_lock_lineage(session, project):
                continue
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job.status == "RUNNING" and job.lease_until <= datetime.now(UTC):
                run = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
                if run.status == "RUNNING":
                    run.status = job.status = "FAILED"
                    run.results = {"code": "POLICY_GENERATION_LEASE_EXPIRED_NO_REPLAY"}
    candidates = _candidates(sessions, "QUEUED", only_run_id=only_run_id)
    if not candidates:
        return {"status": "IDLE"}
    owned = executor is None
    if owned:
        try:
            from cutback.evals.executors import OpenAIExecutor

            executor = OpenAIExecutor()
        except (ValueError, ImportError):
            return {"status": "NO_GENERATOR_API_KEY_OR_SUPPORTED_EXTRA", "provider_calls": 0}
    claimed = False
    run_id = None
    try:
        for job_id, project in candidates:
            with sessions.begin() as session:
                if not try_lock_lineage(session, project):
                    continue
                job = session.get(JobRecord, job_id, with_for_update=True)
                if job.status != "QUEUED":
                    continue
                run = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
                if run.status != "QUEUED":
                    job.status = run.status
                    continue
                run_id = run.id
                parent, _ = program_for_run(session, run)
                if parent is not None and (
                    parent.specification["template"]["source_policy_execution"]["transport"]
                    == "scripted_test_only"
                    and getattr(executor, "synthetic_transport", False) is not True
                ):
                    run.status = job.status = "BLOCKED"
                    job.available_at = None
                    run.results = {
                        "code": "SCRIPTED_POLICY_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR",
                        "provider_calls": 0,
                    }
                    return {"status": "BLOCKED", "run_id": str(run_id), "provider_calls": 0}
                authorize_program_run(
                    session,
                    store,
                    run.id,
                    synthetic_transport=getattr(executor, "synthetic_transport", False),
                )
                parent, _ = program_for_run(session, run)
                if parent is None:
                    raise ValueError("POLICY_GENERATION_APPROVED_PARENT_REQUIRED")
                _, policy, _, _ = authorize_program(
                    session,
                    store,
                    parent,
                    synthetic_transport=getattr(executor, "synthetic_transport", False),
                )
                row = session.get(ExperimentPlanRecord, run.plan_id)
                plan = load_generation(store, project, row.specification)
                value = read_content(
                    session,
                    store,
                    parent,
                    plan.input_payload_id,
                    plan.input_sha256,
                    policy,
                    kind="generation_input",
                )
                if (
                    type(value) is not dict
                    or set(value) != {"input", "training_families"}
                    or type(value["input"]) is not str
                ):
                    raise ValueError("POLICY_GENERATION_FROZEN_INPUT_REQUIRED")
                text = value["input"]
                parent_id, environment_id, deadline = (
                    parent.id,
                    parent.environment_id,
                    parent.deadline_at,
                )
                run.status = job.status = "RUNNING"
                job.attempts += 1
                job.lease_until = min(
                    deadline,
                    datetime.now(UTC) + timedelta(seconds=plan.budget.max_wall_seconds + 30),
                )
                run.call_ledger_version, run.known_cost_requests = 1, 0
                claimed = True
            break
        if not claimed:
            return {"status": "IDLE"}

        def authority(session):
            authorize_program_run(
                session,
                store,
                run_id,
                synthetic_transport=getattr(executor, "synthetic_transport", False),
            )
            active = session.get(ExperimentRunRecord, run_id, populate_existing=True)
            lease = session.get(JobRecord, job_id, populate_existing=True)
            if (
                active.status != "RUNNING"
                or lease.status != "RUNNING"
                or lease.lease_until is None
                or lease.lease_until <= datetime.now(UTC)
            ):
                raise ValueError("POLICY_GENERATION_CANCELLED_OR_LEASE_EXPIRED")

        ledger = ExecutionLedger(
            sessions, run_id, job_id, plan, environment_id, authorize=authority
        )
        local = BudgetLedger(
            plan.budget,
            price_sha256=plan.price_sha256,
            reserve_call=ledger.reserve,
            record_call=ledger.record,
            execution_deadline=time.monotonic()
            + max(0, (deadline - datetime.now(UTC)).total_seconds()),
        )
        with observation_scope("optimization", run_id=run_id):
            result = local.call(executor, plan.baseline, text, traffic_kind="optimization")
        with content_transaction(sessions, store, project) as (session, refs):
            from cutback.server.dataset_lineage import lock_lineage
            from cutback.server.policy_program_db import SourcePolicyProgramRecord

            lock_lineage(session, project)
            authority(session)
            parent = session.get(SourcePolicyProgramRecord, parent_id, with_for_update=True)
            _, policy, _, _ = authorize_program(
                session,
                store,
                parent,
                synthetic_transport=getattr(executor, "synthetic_transport", False),
            )
            output = put_content(
                session,
                store,
                parent,
                {"output": result.output, "complete": result.complete},
                "generation_output",
                policy,
                refs,
                identifier=uuid5(parent.id, f"generation-output-{plan.round_index}"),
                derivation={"run_id": str(run_id), "round_index": plan.round_index},
            )
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id, with_for_update=True)
            run.status = job.status = (
                "COMPLETED" if result.complete and local.billing_complete else "FAILED"
            )
            run.results = {
                "operation": plan.operation,
                "status": run.status,
                "output_payload_id": str(output.id),
                "output_sha256": output.content_hash,
                "requests": run.requests_used,
                "known_cost_requests": run.known_cost_requests,
                "billing_complete": local.billing_complete
                and run.requests_used == run.known_cost_requests,
                "known_spend_subtotal_usd": str(run.spent_usd),
                "quality_evidence": "none_proposal_only",
                "synthetic_transport": getattr(executor, "synthetic_transport", False) is True,
            }
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="policy_generation_worker",
                    action="source_policy_generation_recorded",
                    details={"program_id": str(parent.id), "run_id": str(run.id)},
                )
            )
            status = run.status
        return {"status": status, "run_id": str(run_id)}
    except Exception:
        if run_id is None:
            return {"status": "FAILED", "provider_calls": 0}
        from cutback.server.dataset_lineage import lock_lineage

        with sessions.begin() as session:
            lock_lineage(session, project)
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id, with_for_update=True)
            if run.status in {"QUEUED", "RUNNING"}:
                run.status = job.status = "FAILED"
                run.results = {
                    "code": "POLICY_GENERATION_AUTHORITY_EXECUTION_OR_STORAGE_FAILED_NO_REPLAY",
                    "requests": run.requests_used,
                    "known_cost_requests": run.known_cost_requests,
                    "billing_complete": run.requests_used == run.known_cost_requests,
                }
            status = run.status
        return {"status": status, "run_id": str(run_id)}
    finally:
        if owned:
            executor.close()
