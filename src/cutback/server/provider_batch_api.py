"""Batch preparation/one-use approval; native requests never run inside API handlers."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import Field, JsonValue, field_validator
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.server.dataset_lineage import lock_lineage, reserve_evaluation
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.evaluation_api import require_store
from cutback.server.provider_batch_db import ProviderBatchRecord
from cutback.server.provider_batch_service import (
    authorize_batch,
    batch_inputs,
    frozen_envelope,
    public_batch,
)


class BatchPrepare(StrictModel):
    plan_id: UUID
    provider_storage_approved: Literal[True]
    max_turnaround_seconds: int = Field(default=90000, ge=86400, le=93600)

    @field_validator("provider_storage_approved", mode="before")
    @classmethod
    def explicit_storage_consent(cls, value):
        if value is not True:
            raise ValueError("BATCH_EXPLICIT_PROVIDER_STORAGE_APPROVAL_REQUIRED")
        return value


class BatchApprove(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class BatchControl(StrictModel):
    pass


class BatchRecoveryHint(StrictModel):
    model_key: Identifier
    file_id: str | None = Field(default=None, pattern=r"^file[-_][A-Za-z0-9_-]{1,180}$")
    batch_id: str | None = Field(default=None, pattern=r"^batch_[A-Za-z0-9_-]{1,180}$")


class BatchReconcile(StrictModel):
    hints: list[BatchRecoveryHint] = Field(default_factory=list, max_length=6)


class ProviderBatchReceipt(StrictModel):
    id: UUID
    plan_id: UUID
    run_id: UUID | None
    state: str
    approval_hash: str
    specification: dict[str, JsonValue]
    report: dict[str, JsonValue]
    created_at: datetime
    deadline_at: datetime | None
    provider_batches: list[dict[str, JsonValue]]
    canonical_call_reservations: int
    accounted_active_processing_us: int
    uncertain_active_processing_us: int
    held_active_processing_us: int


def install_provider_batches(app, sessions, authorize, scope):
    def scoped(session, project, identifier):
        row = session.get(ProviderBatchRecord, identifier, with_for_update=True)
        if not row or row.project_id != project:
            raise HTTPException(404, "BATCH_NOT_FOUND")
        return row

    @app.post(
        "/v1/projects/{project_id}/provider-batches",
        status_code=201,
        response_model=ProviderBatchReceipt,
    )
    def prepare(project_id: UUID, body: BatchPrepare, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            previous = session.scalar(
                select(ProviderBatchRecord).where(
                    ProviderBatchRecord.project_id == project_id,
                    ProviderBatchRecord.plan_id == body.plan_id,
                )
            )
            if previous:
                if previous.specification["max_turnaround_seconds"] != body.max_turnaround_seconds:
                    raise HTTPException(409, "BATCH_PREPARATION_SCOPE_ALREADY_FROZEN")
                return public_batch(previous)
            plan_row = session.get(ExperimentPlanRecord, body.plan_id, with_for_update=True)
            try:
                plan, cases, policy, environment, _ = batch_inputs(
                    session,
                    require_store(app),
                    project_id,
                    plan_row,
                    deadline=datetime.now(UTC) + timedelta(seconds=body.max_turnaround_seconds),
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            if plan_row.approval_status != "pending":
                raise HTTPException(409, "BATCH_REQUIRES_UNCONSUMED_NEW_PLAN")
            if (
                session.scalar(
                    select(func.count())
                    .select_from(ProviderBatchRecord)
                    .where(ProviderBatchRecord.project_id == project_id)
                )
                >= 100
            ):
                raise HTTPException(429, "BATCH_PROJECT_QUOTA_EXHAUSTED")
            envelope = frozen_envelope(plan_row, environment, body.max_turnaround_seconds)
            row = ProviderBatchRecord(
                project_id=project_id,
                environment_id=environment.id,
                plan_id=plan_row.id,
                approval_hash=digest(envelope),
                specification=envelope,
                actor=auth["actor"],
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="provider_batch_prepared",
                    details={
                        "batch_id": str(row.id),
                        "approval_hash": row.approval_hash,
                        "provider_storage_approved": True,
                    },
                )
            )
            return public_batch(row)

    @app.get(
        "/v1/projects/{project_id}/provider-batches", response_model=list[ProviderBatchReceipt]
    )
    def batches(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=10000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                public_batch(row)
                for row in session.scalars(
                    select(ProviderBatchRecord)
                    .where(ProviderBatchRecord.project_id == project_id)
                    .order_by(ProviderBatchRecord.created_at.desc(), ProviderBatchRecord.id)
                    .offset(offset)
                    .limit(limit)
                )
            ]

    @app.get(
        "/v1/projects/{project_id}/provider-batches/{batch_id}", response_model=ProviderBatchReceipt
    )
    def batch(project_id: UUID, batch_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return public_batch(scoped(session, project_id, batch_id))

    @app.post(
        "/v1/projects/{project_id}/provider-batches/{batch_id}/approve",
        status_code=202,
        response_model=ProviderBatchReceipt,
    )
    def approve(project_id: UUID, batch_id: UUID, body: BatchApprove, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, batch_id)
            if row.state != "PENDING" or row.approval_hash != body.approval_hash:
                raise HTTPException(409, "BATCH_APPROVAL_CHANGED_OR_CONSUMED")
            try:
                plan, _, _, _, _ = authorize_batch(session, require_store(app), row, starting=True)
                batch_inputs(
                    session,
                    require_store(app),
                    project_id,
                    session.get(ExperimentPlanRecord, row.plan_id),
                    deadline=datetime.now(UTC)
                    + timedelta(seconds=row.specification["max_turnaround_seconds"]),
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            frozen = session.get(ExperimentPlanRecord, row.plan_id, with_for_update=True)
            if frozen.approval_status != "pending":
                raise HTTPException(409, "BATCH_PLAN_APPROVAL_ALREADY_CONSUMED")
            frozen.approval_status, frozen.approved_by = "consumed", auth["actor"]
            run = ExperimentRunRecord(
                project_id=project_id, plan_id=frozen.id, status="QUEUED", results={}
            )
            session.add(run)
            session.flush()
            run.results = {
                "lineage": reserve_evaluation(session, project_id, frozen.dataset_id, plan, run.id),
                "execution_mode": "provider_batch",
            }
            job = JobRecord(
                project_id=project_id,
                run_id=run.id,
                status="QUEUED",
                execution_kind="provider_batch",
            )
            session.add(job)
            session.flush()
            row.run_id, row.job_id, row.approved_by, row.state = (
                run.id,
                job.id,
                auth["actor"],
                "QUEUED",
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="provider_batch_budget_and_storage_approved",
                    details={
                        "batch_id": str(row.id),
                        "approval_hash": body.approval_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
            return public_batch(row)

    @app.post(
        "/v1/projects/{project_id}/provider-batches/{batch_id}/cancel",
        response_model=ProviderBatchReceipt,
    )
    def cancel(project_id: UUID, batch_id: UUID, body: BatchControl, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, batch_id)
            if row.state in {"PENDING", "QUEUED"} and row.started_at is None:
                row.state, row.completed_at = "CANCELLED", datetime.now(UTC)
                row.report = {
                    "code": "BATCH_CANCELLED_BEFORE_EXECUTION",
                    "billing_complete": True,
                    "final_state": "CANCELLED",
                }
                if row.run_id:
                    run = session.get(ExperimentRunRecord, row.run_id, with_for_update=True)
                    job = session.get(JobRecord, row.job_id, with_for_update=True)
                    run.status = job.status = "CANCELLED"
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="provider_batch_cancelled_before_execution",
                        details={"batch_id": str(row.id)},
                    )
                )
                return public_batch(row)
            if row.state not in {"COMPLETED", "CANCELLED", "FAILED"}:
                row.state = "CANCEL_REQUESTED"
                row.available_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="provider_batch_cancel_requested",
                    details={"batch_id": str(row.id)},
                )
            )
            return public_batch(row)

    @app.post(
        "/v1/projects/{project_id}/provider-batches/{batch_id}/reconcile",
        response_model=ProviderBatchReceipt,
    )
    def reconcile(project_id: UUID, batch_id: UUID, body: BatchReconcile, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = scoped(session, project_id, batch_id)
            if row.state not in {"UNCERTAIN", "CANCEL_REQUESTED"}:
                raise HTTPException(409, "BATCH_RECONCILIATION_REQUIRES_UNCERTAIN_SUBMISSION")
            if not row.pieces:
                raise HTTPException(409, "BATCH_INTERRUPTED_PREPARATION_REQUIRES_CANCELLATION")
            import copy

            pieces = copy.deepcopy(row.pieces)
            if len({hint.model_key for hint in body.hints}) != len(body.hints):
                raise HTTPException(422, "BATCH_RECOVERY_HINTS_MUST_BE_UNIQUE")
            for hint in body.hints:
                part = next((part for part in pieces if part["model_key"] == hint.model_key), None)
                if (
                    not part
                    or (hint.file_id is None) == (hint.batch_id is None)
                    or (hint.file_id and part["state"] != "UPLOADING")
                    or (hint.batch_id and part["state"] != "SUBMITTING")
                ):
                    raise HTTPException(409, "BATCH_RECOVERY_HINT_OUTSIDE_FROZEN_PHASE")
                part["hint_file_id"], part["hint_batch_id"] = hint.file_id, hint.batch_id
            row.pieces = pieces
            if row.state != "CANCEL_REQUESTED":
                row.state = "RECONCILE_REQUESTED"
            row.available_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="provider_batch_read_only_reconciliation_requested",
                    details={"batch_id": str(row.id)},
                )
            )
            return public_batch(row)
