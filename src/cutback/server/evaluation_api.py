"""Control-plane dataset authorization, immutable plans and audited human decisions."""

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import event as sql_event
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.artifacts import configured_store
from cutback.evals.datasets import (
    CapturePolicy,
    DatasetImport,
    Sample,
    digest,
    prepare_dataset,
    redact,
)
from cutback.evals.experiments import CriterionSpec, ExperimentPlan, ModelSpec, plan_summary
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
from cutback.server.plan_artifacts import freeze_plan, load_plan


def policy_object_count(session, policy_id):
    from cutback.server.enterprise_db import CacheEntry

    return sum(
        session.scalar(select(func.count()).select_from(cls).where(cls.policy_id == policy_id)) or 0
        for cls in (PayloadSample, CacheEntry)
    )


class PlanInput(StrictModel):
    dataset_id: UUID
    specification: ExperimentPlan


class ApproveInput(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class QueueInput(StrictModel):
    plan_id: UUID


class DraftInput(StrictModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 .:/_-]*$"
    )
    criteria: list[CriterionSpec] = Field(default_factory=list, max_length=8)
    retain_case_outputs: bool = False
    dataset_id: UUID
    baseline: ModelSpec
    candidates: list[ModelSpec] | None = Field(default=None, min_length=1, max_length=5)
    minimum: float | None = Field(default=None, ge=0, le=1)
    max_usd: str = Field(default="0", max_length=30)
    max_requests: int = Field(default=100, ge=1, le=10000)
    repeats: int = Field(default=1, ge=1, le=5)
    pilot_samples: int = Field(default=0, ge=0, le=100)
    pilot_minimum: float | None = Field(default=None, ge=0, le=1)
    max_wall_seconds: int = Field(default=300, ge=1, le=3600)


class ReviewInput(StrictModel):
    decision: Literal["accepted_for_staging", "rejected"]
    reason: Identifier


class ContentPurpose(StrictModel):
    purpose: Identifier


def require_store(app):
    if os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower() != "true":
        raise HTTPException(403, "PAYLOAD_STORAGE_DISABLED")
    try:
        return getattr(app.state, "artifact_store", None) or configured_store()
    except (ValueError, ImportError, OSError):
        raise HTTPException(503, "PAYLOAD_STORAGE_NOT_CONFIGURED") from None


def active_policy(session, project, policy_id):
    row = session.get(CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True)
    if not row or row.project_id != project or not row.enabled:
        raise HTTPException(403, "CAPTURE_POLICY_DISABLED_OR_OUT_OF_SCOPE")
    policy = CapturePolicy.model_validate(row.policy)
    if policy.expires_at <= datetime.now(UTC):
        raise HTTPException(403, "CAPTURE_POLICY_EXPIRED")
    return row, policy


def dataset_manifest(session, dataset):
    rows = session.scalars(
        select(DatasetItem).where(DatasetItem.dataset_id == dataset.id).order_by(DatasetItem.id)
    ).all()
    return {
        "immutable_hash": dataset.immutable_hash,
        "items": [{"split": row.split} for row in rows],
    }


def install_evaluation(app, sessions, authorize, scope):
    @app.get("/v1/ingest/capture-policies/{policy_id}")
    def capture_policy_for_sdk(policy_id: UUID, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        require_store(app)
        with sessions() as session:
            _, policy = active_policy(session, auth["project_id"], policy_id)
            environment = session.get(Environment, auth["environment_id"])
            if environment.name not in policy.environments:
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
        return {"policy": policy.model_dump(mode="json")}

    @app.post("/v1/ingest/samples", status_code=201)
    def capture_sample(body: Sample, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        store = require_store(app)
        project = auth["project_id"]
        ref = None
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project, body.policy_id)
                environment = session.get(Environment, auth["environment_id"])
                if (
                    environment.name != body.environment
                    or not policy.permits(body.environment, body.workflow)
                    or body.provider not in policy.allowed_providers
                ):
                    raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                if body.sample_id.int / (2**128) >= policy.sample_rate:
                    raise HTTPException(403, "SAMPLE_OUTSIDE_APPROVED_RATE")
                existing = session.get(PayloadSample, body.sample_id)
                if existing:
                    if existing.project_id != project:
                        raise HTTPException(403, "SAMPLE_SCOPE_DENIED")
                    return {"status": "duplicate", "sample_id": str(body.sample_id)}
                count = policy_object_count(session, body.policy_id)
                if count >= policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                case = redact(
                    {
                        "id": str(body.sample_id),
                        "input": body.input,
                        "reference": body.reference,
                        "gold": None,
                        "group": None,
                        "stratum": "default",
                    },
                    policy.never_capture_fields,
                )
                blob = json.dumps(case).encode()
                if len(blob) > policy.max_payload_bytes:
                    raise HTTPException(413, "SAMPLE_EXCEEDS_POLICY_BYTES")
                ref = store.put(project, blob, policy.retention_days)
                session.add(
                    PayloadSample(
                        id=body.sample_id,
                        project_id=project,
                        policy_id=body.policy_id,
                        object_ref=ref,
                        content_hash=digest(case),
                        expires_at=datetime.now(UTC) + timedelta(days=policy.retention_days),
                        metadata_safe={
                            "environment": body.environment,
                            "workflow": body.workflow,
                            "event_id": str(body.event_id),
                            "ordinal": 0,
                            "redaction": policy.redaction_version,
                        },
                    )
                )
            return {"status": "stored", "sample_id": str(body.sample_id)}
        except BaseException:
            if ref:
                store.delete(project, ref)
            raise

    @app.get("/v1/projects/{project_id}/experiments/plans")
    def plans(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "dataset_id": str(row.dataset_id),
                    "specification": row.specification["public_specification"],
                    "plan_hash": row.specification["plan_hash"],
                    "approval_status": row.approval_status,
                }
                for row in session.scalars(
                    select(ExperimentPlanRecord)
                    .where(ExperimentPlanRecord.project_id == project_id)
                    .order_by(ExperimentPlanRecord.id)
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.post("/v1/projects/{project_id}/capture-policies", status_code=201)
    def create_policy(project_id: UUID, body: CapturePolicy, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        if body.enabled:
            require_store(app)
            if body.expires_at <= datetime.now(UTC):
                raise HTTPException(422, "POLICY_ALREADY_EXPIRED")
        with sessions.begin() as session:
            row = CapturePolicyRecord(
                project_id=project_id,
                enabled=body.enabled,
                approved_by=auth["actor"],
                policy=body.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="capture_policy_created",
                    details={"policy_id": str(row.id), "enabled": body.enabled},
                )
            )
        return {"id": str(row.id), "enabled": row.enabled}

    @app.get("/v1/projects/{project_id}/capture-policies")
    def policies(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "enabled": row.enabled,
                    "policy": row.policy,
                    "approved_by": row.approved_by,
                }
                for row in session.scalars(
                    select(CapturePolicyRecord)
                    .where(CapturePolicyRecord.project_id == project_id)
                    .order_by(CapturePolicyRecord.created_at.desc())
                    .limit(100)
                )
            ]

    @app.delete("/v1/projects/{project_id}/capture-policies/{policy_id}")
    def revoke_policy(project_id: UUID, policy_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(CapturePolicyRecord, policy_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "POLICY_NOT_FOUND")
            row.enabled = False
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="capture_policy_revoked",
                    details={"policy_id": str(policy_id)},
                )
            )
        return {"status": "revoked", "future_reads_and_runs": "denied"}

    @app.post("/v1/projects/{project_id}/datasets", status_code=201)
    def import_dataset(project_id: UUID, body: DatasetImport, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        stored = []
        try:
            with sessions.begin() as session:
                try:
                    policy_id = UUID(body.policy_id)
                except ValueError:
                    raise HTTPException(422, "INVALID_POLICY_ID") from None
                _, policy = active_policy(session, project_id, policy_id)
                if not policy.permits(body.environment, body.workflow):
                    raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                if body.source_type == "synthetic_fixture" and policy.rights_basis != "synthetic":
                    raise HTTPException(422, "SYNTHETIC_PROVENANCE_POLICY_MISMATCH")
                session.get(CapturePolicyRecord, policy_id, with_for_update=True)
                existing = policy_object_count(session, policy_id)
                if existing + len(body.cases) > policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                # Serialize policy consumption across concurrent imports before checking quota.
                clean = DatasetImport.model_validate(
                    redact(body.model_dump(mode="json"), policy.never_capture_fields)
                )
                prepared = prepare_dataset(clean)
                provenance = clean.model_dump(mode="json", exclude={"cases"})
                provenance.update(
                    {
                        "split_counts": prepared["split_counts"],
                        "task_inference": prepared["task_inference"],
                        "split_strategy": prepared["split_strategy"],
                        "group_count": prepared["group_count"],
                        "strata": prepared["strata"],
                        "warnings": prepared["warnings"],
                        "case_count": len(clean.cases),
                        "max_input_bytes": max(len(case.input.encode()) for case in clean.cases),
                        "redaction": policy.redaction_version,
                    }
                )
                dataset = DatasetRecord(
                    project_id=project_id,
                    workflow_id=body.workflow,
                    immutable_hash=prepared["immutable_hash"],
                    provenance=provenance,
                )
                session.add(dataset)
                session.flush()
                for ordinal, item in enumerate(prepared["items"]):
                    blob = json.dumps(item["case"]).encode()
                    if len(blob) > policy.max_payload_bytes:
                        raise HTTPException(413, "SAMPLE_EXCEEDS_POLICY_BYTES")
                    ref = store.put(project_id, blob, policy.retention_days)
                    stored.append(ref)
                    payload = PayloadSample(
                        project_id=project_id,
                        policy_id=policy_id,
                        object_ref=ref,
                        content_hash=digest(item["case"]),
                        expires_at=datetime.now(UTC) + timedelta(days=policy.retention_days),
                        metadata_safe={"redaction": policy.redaction_version, "ordinal": ordinal},
                    )
                    session.add(payload)
                    session.flush()
                    session.add(
                        DatasetItem(
                            dataset_id=dataset.id,
                            payload_id=payload.id,
                            split=item["split"],
                            group_hash=item["group_hash"],
                        )
                    )
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="dataset_imported",
                        details={"dataset_id": str(dataset.id), "cases": len(clean.cases)},
                    )
                )
            return {
                "id": str(dataset.id),
                "immutable_hash": dataset.immutable_hash,
                "provenance": provenance,
                "content": "encrypted_separate_store",
            }
        except BaseException:
            for ref in stored:
                store.delete(project_id, ref)
            raise

    @app.get("/v1/projects/{project_id}/datasets")
    def datasets(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "hash": row.immutable_hash,
                    "workflow": row.workflow_id,
                    "provenance": row.provenance,
                    "created_at": row.created_at.isoformat(),
                }
                for row in session.scalars(
                    select(DatasetRecord)
                    .where(DatasetRecord.project_id == project_id)
                    .order_by(DatasetRecord.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.post("/v1/projects/{project_id}/experiments/draft", status_code=201)
    def create_draft(project_id: UUID, body: DraftInput, auth=Depends(authorize)):
        from cutback.evals.planning import draft_plan

        scope(auth, project_id, ("analyst",))
        with sessions() as session:
            row = session.get(DatasetRecord, body.dataset_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            metadata = {
                "hash": row.immutable_hash,
                "workflow": row.workflow_id,
                "provenance": row.provenance,
            }
        try:
            spec = draft_plan(
                metadata,
                body.baseline,
                candidates=body.candidates,
                minimum=body.minimum,
                max_usd=body.max_usd,
                max_requests=body.max_requests,
                repeats=body.repeats,
                pilot_samples=body.pilot_samples,
                pilot_minimum=body.pilot_minimum,
                max_wall_seconds=body.max_wall_seconds,
                criteria=body.criteria,
                retain_case_outputs=body.retain_case_outputs,
            )
            spec = spec.model_copy(update={"name": body.name})
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                422, code if code.isupper() and len(code) < 100 else "INVALID_DRAFT_CONTRACT"
            ) from None
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=spec), auth
        )

    @app.post("/v1/projects/{project_id}/experiments/plans", status_code=201)
    def create_plan(project_id: UUID, body: PlanInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            from cutback.server.stages import validate_stage

            try:
                validate_stage(session, require_store(app), project_id, body.specification)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            _, policy = active_policy(session, project_id, UUID(dataset.provenance["policy_id"]))
            if body.specification.workflow != dataset.workflow_id or any(
                p not in policy.allowed_providers for p in body.specification.approved_providers
            ):
                raise HTTPException(403, "DATASET_OR_VENDOR_SCOPE_DENIED")
            try:
                summary = plan_summary(body.specification, dataset_manifest(session, dataset))
            except ValueError:
                raise HTTPException(422, "DATASET_HASH_MISMATCH") from None
            if (
                session.scalar(
                    select(func.count())
                    .select_from(ExperimentPlanRecord)
                    .where(ExperimentPlanRecord.project_id == project_id)
                )
                >= 1000
            ):
                raise HTTPException(429, "PROJECT_PLAN_QUOTA_EXCEEDED")
            store = require_store(app)
            try:
                frozen = freeze_plan(
                    store, project_id, body.specification, policy, dataset.provenance["policy_id"]
                )
            except ValueError:
                raise HTTPException(422, "PLAN_CONTENT_POLICY_DENIED") from None
            sql_event.listen(
                session,
                "after_rollback",
                lambda unused: store.delete(project_id, frozen["artifact_ref"]),
                once=True,
            )
            row = ExperimentPlanRecord(
                project_id=project_id,
                dataset_id=dataset.id,
                specification=frozen,
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_planned",
                    details={"plan_id": str(row.id), **summary},
                )
            )
        return {"id": str(row.id), "status": "READY_FOR_APPROVAL", **summary}

    @app.post("/v1/projects/{project_id}/experiments/plans/{plan_id}/content")
    def read_plan_content(
        project_id: UUID, plan_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            row = session.get(ExperimentPlanRecord, plan_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                plan = load_plan(store, project_id, row.specification)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="plan_content_read",
                    details={"plan_id": str(plan_id), "purpose": body.purpose},
                )
            )
        return {
            "specification": plan.model_dump(mode="json"),
            "plan_hash": row.specification["plan_hash"],
        }

    @app.post("/v1/projects/{project_id}/experiments/plans/{plan_id}/approve")
    def approve_plan(project_id: UUID, plan_id: UUID, body: ApproveInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(ExperimentPlanRecord, plan_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            if row.approval_status != "pending":
                raise HTTPException(409, "PLAN_ALREADY_DECIDED")
            if row.specification["plan_hash"] != body.plan_hash:
                raise HTTPException(409, "PLAN_CHANGED")
            row.approval_status, row.approved_by = "approved", auth["actor"]
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_budget_approved",
                    details={
                        "plan_id": str(row.id),
                        "plan_hash": body.plan_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
        return {"status": "APPROVED", "provider_calls_made": 0}

    @app.post("/v1/projects/{project_id}/experiments", status_code=202)
    def queue_run(project_id: UUID, body: QueueInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            plan = session.get(ExperimentPlanRecord, body.plan_id, with_for_update=True)
            if not plan or plan.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            if plan.approval_status != "approved":
                raise HTTPException(403, "NO_API_BUDGET_OR_APPROVAL_ALREADY_CONSUMED")
            from cutback.server.stages import validate_stage

            try:
                validate_stage(
                    session,
                    require_store(app),
                    project_id,
                    load_plan(require_store(app), project_id, plan.specification),
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            plan.approval_status = (
                "consumed"  # A budget approval authorizes one run, not unlimited runs.
            )
            row = ExperimentRunRecord(
                project_id=project_id, plan_id=plan.id, status="QUEUED", results={}
            )
            session.add(row)
            session.flush()
            session.add(JobRecord(project_id=project_id, run_id=row.id, status="QUEUED"))
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_queued",
                    details={"run_id": str(row.id)},
                )
            )
        return {"id": str(row.id), "status": "QUEUED", "provider_calls_made_in_http": 0}

    @app.get("/v1/projects/{project_id}/experiments")
    def runs(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.execute(
                select(ExperimentRunRecord, ExperimentPlanRecord)
                .join(ExperimentPlanRecord, ExperimentPlanRecord.id == ExperimentRunRecord.plan_id)
                .where(ExperimentRunRecord.project_id == project_id)
                .order_by(ExperimentRunRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [
                {
                    "id": str(row.id),
                    "plan_id": str(row.plan_id),
                    "status": row.status,
                    "reserved_usd": str(row.reserved_usd),
                    "spent_usd": str(row.spent_usd),
                    "requests": row.requests_used,
                    "block_reason": row.results.get("code") if row.status == "BLOCKED" else None,
                    "retry_after": row.results.get("retry_after")
                    if row.status == "BLOCKED"
                    else None,
                    "specification": plan.specification["public_specification"],
                    "created_at": row.created_at.isoformat(),
                    "decisions": row.results.get("recommendations", []),
                }
                for row, plan in rows
            ]

    @app.get("/v1/projects/{project_id}/experiments/{run_id}")
    def run_report(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(ExperimentRunRecord, run_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            plan = session.get(ExperimentPlanRecord, row.plan_id)
            return {
                "id": str(row.id),
                "status": row.status,
                "results": row.results,
                "specification": plan.specification["public_specification"],
                "reserved_usd": str(row.reserved_usd),
                "spent_usd": str(row.spent_usd),
                "requests": row.requests_used,
            }

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cancel")
    def cancel(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            if row.status not in {"QUEUED", "BLOCKED", "RUNNING"}:
                raise HTTPException(409, "RUN_TERMINAL")
            row.status = "CANCELLED"
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == row.id).with_for_update()
            )
            if job:
                job.status = "CANCELLED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_cancelled",
                    details={"run_id": str(row.id)},
                )
            )
        return {"status": "CANCELLED", "inflight_call": "may_finish_no_further_calls"}

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/recheck")
    def recheck_blocked(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            if row.status != "BLOCKED" or row.requests_used:
                raise HTTPException(409, "ONLY_UNSTARTED_BLOCKED_RUNS_CAN_RECHECK")
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == row.id).with_for_update()
            )
            row.status = job.status = "QUEUED"
            job.available_at = None
            row.results = {}
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_eligibility_rechecked",
                    details={"run_id": str(run_id), "provider_calls": 0},
                )
            )
        return {"status": "QUEUED", "provider_calls": 0}

    @app.get("/v1/projects/{project_id}/recommendations")
    def recommendations(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "run_id": str(row.run_id),
                    "status": row.status,
                    "evidence": row.evidence,
                }
                for row in session.scalars(
                    select(RecommendationRecord)
                    .where(RecommendationRecord.project_id == project_id)
                    .order_by(RecommendationRecord.id)
                    .limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/recommendations/{recommendation_id}/review")
    def review(
        project_id: UUID, recommendation_id: UUID, body: ReviewInput, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(RecommendationRecord, recommendation_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RECOMMENDATION_NOT_FOUND")
            if row.status != "pending":
                raise HTTPException(409, "RECOMMENDATION_ALREADY_REVIEWED")
            if body.decision == "accepted_for_staging" and (
                not row.evidence.get("quality_gate_passed")
                or not row.evidence.get("cheaper_on_sample")
            ):
                raise HTTPException(409, "REQUIRED_COST_AND_QUALITY_GATES_NOT_PASSED")
            row.status = body.decision
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="recommendation_reviewed",
                    details={"recommendation_id": str(row.id), **body.model_dump()},
                )
            )
        return {"status": body.decision, "deployment": "not_performed"}

    return import_dataset
