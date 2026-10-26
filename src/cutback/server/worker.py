"""Postgres-backed queue. Crash recovery never repeats a potentially billable call."""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from cutback.evals.datasets import CapturePolicy, DatasetImport, digest
from cutback.evals.experiments import provider_models, run
from cutback.server.db import (
    AuditEvent,
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
    RecommendationRecord,
)
from cutback.server.plan_artifacts import load_plan, public_config


def load_dataset(session, dataset, store, *, now=None):
    now = now or datetime.now(UTC)
    policy_row = session.get(CapturePolicyRecord, dataset.provenance["policy_id"])
    if not policy_row or policy_row.project_id != dataset.project_id or not policy_row.enabled:
        raise ValueError("CAPTURE_POLICY_REVOKED")
    policy = CapturePolicy.model_validate(policy_row.policy)
    if not policy.permits(dataset.provenance["environment"], dataset.workflow_id):
        raise ValueError("CAPTURE_POLICY_EXPIRED_OR_OUT_OF_SCOPE")
    items = session.scalars(select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)).all()
    ordered = []
    expiries = []
    for item in items:
        payload = session.get(PayloadSample, item.payload_id)
        if payload.project_id != dataset.project_id or payload.expires_at <= now:
            raise ValueError("PAYLOAD_EXPIRED_OR_OUT_OF_SCOPE")
        case = json.loads(store.get(dataset.project_id, payload.object_ref))
        expiries.append(payload.expires_at)
        if digest(case) != payload.content_hash:
            raise ValueError("PAYLOAD_INTEGRITY_FAILURE")
        ordered.append(
            (
                payload.metadata_safe["ordinal"],
                {"case": case, "split": item.split, "group_hash": item.group_hash},
            )
        )
    ordered.sort(key=lambda pair: pair[0])
    fields = set(DatasetImport.model_fields) - {"cases"}
    original = {k: v for k, v in dataset.provenance.items() if k in fields}
    original["cases"] = [item["case"] for _, item in ordered]
    if (
        digest(DatasetImport.model_validate(original).model_dump(mode="json"))
        != dataset.immutable_hash
    ):
        raise ValueError("DATASET_INTEGRITY_FAILURE")
    return {
        "immutable_hash": dataset.immutable_hash,
        "items": [item for _, item in ordered],
        "expires_at": min(expiries).isoformat(),
    }, policy


def reserve_request(sessions, run_id, job_id, budget, amount):
    with sessions.begin() as session:
        record = session.get(ExperimentRunRecord, run_id, with_for_update=True)
        job = session.get(JobRecord, job_id)
        if (
            record.status != "RUNNING"
            or not job.lease_until
            or job.lease_until <= datetime.now(UTC)
        ):
            raise ValueError("RUN_CANCELLED_OR_LEASE_EXPIRED")
        if (
            record.requests_used >= budget.max_requests
            or record.reserved_usd + amount > budget.max_usd
        ):
            raise ValueError("BUDGET_EXHAUSTED")
        record.reserved_usd += amount
        record.requests_used += 1


def run_one(sessions, store, *, executor=None, execution_enabled=False):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    owned_executor = executor is None
    if owned_executor:
        from cutback.evals.executors import ConfiguredExecutor

        try:
            executor = ConfiguredExecutor()
        except (ValueError, ImportError):
            return {"status": "NO_EXPERIMENT_API_KEY_OR_SUPPORTED_EXTRA", "provider_calls": 0}
    try:
        with sessions.begin() as session:
            now = datetime.now(UTC)
            # Expired leases become failed, never requeued: a lost HTTP response may be billable.
            expired = session.scalars(
                select(JobRecord)
                .where(JobRecord.status == "RUNNING", JobRecord.lease_until < now)
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
                .where(JobRecord.status == "QUEUED")
                .order_by(JobRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return {"status": "IDLE"}
            record = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
            if record.status != "QUEUED":
                job.status = record.status
                return {"status": record.status}
            plan_row = session.get(ExperimentPlanRecord, record.plan_id)
            try:
                plan = load_plan(store, record.project_id, plan_row.specification)
            except Exception:
                job.status = record.status = "FAILED"
                record.results = {"code": "PLAN_ARTIFACT_EXPIRED_OR_UNAVAILABLE"}
                return {"status": "FAILED", "provider_calls": 0}
            if owned_executor:
                providers = {
                    target.provider
                    for model in [
                        plan.baseline,
                        *plan.candidates,
                        *([plan.judge] if plan.judge else []),
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
                    record.results = {
                        "code": "NO_EXPERIMENT_KEY_OR_LOCAL_ENDPOINT",
                        "next_action": "configure approved worker credentials; no calls made",
                    }
                    return {
                        "status": "BLOCKED",
                        "code": "NO_EXPERIMENT_KEY_OR_LOCAL_ENDPOINT",
                        "provider_calls": 0,
                    }
            record.status, job.status = "RUNNING", "RUNNING"
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
                dataset_row = session.get(DatasetRecord, dataset_id)
                dataset, policy = load_dataset(session, dataset_row, store)
                if any(
                    provider not in policy.allowed_providers for provider in plan.approved_providers
                ):
                    raise ValueError("PROVIDER_POLICY_DENIED")
                provenance = dataset_row.provenance
                from cutback.server.stages import validate_stage

                validate_stage(session, store, project_id, plan)

            def reserve(amount):
                if datetime.now(UTC) >= min(
                    datetime.fromisoformat(dataset["expires_at"]),
                    datetime.fromisoformat(plan_row.specification["expires_at"]),
                ):
                    raise ValueError("EXPERIMENT_DATA_OR_PLAN_EXPIRED")
                # Recheck revocation/expiry between calls, not only at run startup.
                with sessions() as session:
                    row = session.get(CapturePolicyRecord, provenance["policy_id"])
                    if (
                        not row
                        or not row.enabled
                        or not CapturePolicy.model_validate(row.policy).permits(
                            provenance["environment"], plan.workflow
                        )
                    ):
                        raise ValueError("CAPTURE_POLICY_REVOKED_OR_EXPIRED")
                reserve_request(sessions, run_id, job_id, plan.budget, amount)

            from cutback.server.case_evidence import recorder

            results = run(
                plan,
                dataset,
                executor,
                approval_hash=plan_row.specification["plan_hash"],
                independent_labels=provenance["independent_labels"],
                representative=provenance["representative"]
                and provenance["source_type"] != "synthetic_fixture",
                reserve=reserve,
                record_case=recorder(sessions, store, project_id, run_id, provenance["policy_id"])
                if plan.retain_case_outputs
                else None,
            )
            results["case_outputs_retained"] = plan.retain_case_outputs
        except Exception:
            results = {
                "status": "FAILED",
                "code": "DATASET_POLICY_OR_EXECUTION_FAILED",
                "recommendations": [],
            }
        with sessions.begin() as session:
            record = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id)
            results = public_config(results)
            record.results = results
            if record.status == "RUNNING":
                record.status = results["status"]
            job.status = record.status
            record.spent_usd = Decimal(results.get("known_spend_subtotal_usd", "0"))
            if record.status == "COMPLETED":
                for recommendation in results["recommendations"]:
                    session.add(
                        RecommendationRecord(
                            project_id=project_id,
                            run_id=run_id,
                            status="pending",
                            evidence={
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
