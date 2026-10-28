"""Reviewed finite metadata observation policies and scoped history."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import AwareDatetime, Field, StrictBool, TypeAdapter, field_validator
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.core.outcome_watches import (
    OutcomeWatchEnvelope,
    OutcomeWatchIntent,
    OutcomeWatchSpec,
    Sha256,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import ChangeProposal
from cutback.server.economics_api import environment
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.improvement_api import ImprovementReport
from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow
from cutback.server.outcome_watches import audit, context, source_change_run_family
from cutback.server.principals import principal_role
from cutback.server.traces import fingerprint


class WatchApproval(StrictModel):
    approval_hash: Sha256
    approval_ref: Identifier


class WatchTransition(StrictModel):
    approval_hash: Sha256
    enabled: StrictBool


class WatchReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    approval_hash: Sha256
    specification: OutcomeWatchEnvelope
    status: str
    enabled: bool
    approved_at: datetime | None
    next_run_at: datetime
    windows_processed: int
    last_window_index: int
    windows_skipped: int
    last_code: str | None
    retained_windows: int
    created_at: datetime
    provider_calls_authorized: int = 0
    traffic_changes_authorized: bool = False


class WatchPage(StrictModel):
    items: list[WatchReceipt]
    next_after: UUID | None


class WatchWindowEvidence(StrictModel):
    contract: Literal["cutback.outcome-watch-window.v1"]
    status: Literal["COMPLETED", "MISSING_EVIDENCE", "BLOCKED"]
    approval_hash: Sha256
    window_index: int
    start: str = Field(json_schema_extra={"format": "date-time"})
    end: str = Field(json_schema_extra={"format": "date-time"})
    declarations_as_of: str = Field(json_schema_extra={"format": "date-time"})
    candidate_snapshot_id: UUID | None
    improvement_id: UUID | None
    report: ImprovementReport | None
    next_action: str
    reasons: list[str]
    skipped_previous_windows: int
    release_linkage: Literal["owner_attested_not_deployment_verified"]
    action_authorized: Literal[False]
    provider_calls: Literal[0]
    traffic_changed: Literal[False]

    @field_validator("start", "end", "declarations_as_of")
    @classmethod
    def preserve_frozen_time_representation(cls, value):
        TypeAdapter(AwareDatetime).validate_python(value)
        return value


class WatchWindowReceipt(StrictModel):
    id: UUID
    evidence_sha256: Sha256
    evidence: WatchWindowEvidence
    created_at: datetime


class WatchWindowPage(StrictModel):
    items: list[WatchWindowReceipt]
    next_after: int | None
    windows_processed: int
    retained_windows: int
    evidence_scope: str = "retained_frozen_declared_windows_not_population_or_causal_verification"


def receipt(session, row):
    return WatchReceipt(
        id=row.id,
        environment_id=row.environment_id,
        approval_hash=row.approval_hash,
        specification=OutcomeWatchEnvelope.model_validate(row.specification),
        status=row.status,
        enabled=row.enabled,
        approved_at=row.approved_at,
        next_run_at=row.next_run_at,
        windows_processed=row.windows_processed,
        last_window_index=row.last_window_index,
        windows_skipped=row.windows_skipped,
        last_code=row.last_code,
        created_at=row.created_at,
        retained_windows=session.scalar(
            select(func.count())
            .select_from(OutcomeWatchWindow)
            .where(
                OutcomeWatchWindow.project_id == row.project_id,
                OutcomeWatchWindow.watch_record_id == row.id,
            )
        ),
    )


def load(session, project, identifier):
    row = session.get(OutcomeWatch, identifier)
    if not row or row.project_id != project:
        raise HTTPException(404, "OUTCOME_WATCH_NOT_FOUND")
    return row


def validate_approval(session, row, supplied_hash, now):
    if supplied_hash != row.approval_hash or fingerprint(row.specification) != row.approval_hash:
        raise HTTPException(409, "OUTCOME_WATCH_HASH_CHANGED")
    envelope = OutcomeWatchEnvelope.model_validate(row.specification)
    if (envelope.project_id, envelope.environment_id) != (row.project_id, row.environment_id):
        raise HTTPException(409, "OUTCOME_WATCH_SCOPE_CHANGED")
    spec = envelope.policy
    if now >= spec.expires_at or row.windows_processed >= spec.max_windows:
        raise HTTPException(409, "OUTCOME_WATCH_EXPIRED_OR_EXHAUSTED")
    context(session, row.project_id, row.environment_id, spec)
    return spec


def install_outcome_watches(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/outcome-watches/draft",
        response_model=WatchReceipt,
        status_code=201,
    )
    def draft(
        project_id: UUID, environment_id: UUID, body: OutcomeWatchIntent, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            baseline = session.get(TaskCostSnapshot, body.baseline_snapshot_id)
            change = session.get(ChangeProposal, body.change_id)
            if not baseline or (baseline.project_id, baseline.environment_id) != (
                project_id,
                environment_id,
            ):
                raise HTTPException(404, "OUTCOME_WATCH_BASELINE_NOT_FOUND")
            if not change or change.project_id != project_id:
                raise HTTPException(404, "OUTCOME_WATCH_CHANGE_NOT_FOUND")
            runs = source_change_run_family(session, project_id, change)
            try:
                specification = OutcomeWatchSpec.model_validate(
                    {
                        **body.model_dump(mode="json"),
                        "baseline_sha256": baseline.evidence_sha256,
                        "source_diff_sha256": change.evidence.get("diff_sha256"),
                        "optimization_run_ids": [str(identifier) for identifier in runs],
                    }
                )
            except ValueError:
                raise HTTPException(409, "OUTCOME_WATCH_SOURCE_REFERENCE_INVALID") from None
        # The create operation rechecks the exact derived references under its own
        # project transaction. A concurrent mutation fails instead of being trusted.
        return propose(project_id, environment_id, specification, auth)

    @app.post(
        "/v1/projects/{project_id}/outcome-watches", response_model=WatchReceipt, status_code=201
    )
    def propose(
        project_id: UUID, environment_id: UUID, body: OutcomeWatchSpec, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("analyst",))
        envelope = OutcomeWatchEnvelope(
            project_id=project_id, environment_id=environment_id, policy=body
        )
        document = envelope.model_dump(mode="json")
        digest = fingerprint(document)
        now = datetime.now(UTC)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            environment(session, project_id, environment_id)
            existing = session.scalar(
                select(OutcomeWatch).where(
                    OutcomeWatch.project_id == project_id, OutcomeWatch.watch_id == body.watch_id
                )
            )
            if existing:
                if existing.approval_hash != digest:
                    raise HTTPException(409, "OUTCOME_WATCH_REQUEST_CONFLICT")
                return receipt(session, existing)
            if not now < body.expires_at <= now + timedelta(days=30):
                raise HTTPException(422, "OUTCOME_WATCH_MUST_EXPIRE_WITHIN_30_DAYS")
            if body.first_window_end - timedelta(seconds=body.window_seconds) < now - timedelta(
                days=30
            ):
                raise HTTPException(422, "OUTCOME_WATCH_FIRST_WINDOW_OUTSIDE_RETENTION_BOUND")
            context(session, project_id, environment_id, body)
            count = session.scalar(
                select(func.count())
                .select_from(OutcomeWatch)
                .where(OutcomeWatch.project_id == project_id)
            )
            if count >= 100:
                raise HTTPException(409, "OUTCOME_WATCH_PROJECT_POLICY_QUOTA")
            row = OutcomeWatch(
                project_id=project_id,
                environment_id=environment_id,
                watch_id=body.watch_id,
                approval_hash=digest,
                specification=document,
                next_run_at=body.first_window_end + timedelta(seconds=body.grace_seconds),
            )
            session.add(row)
            session.flush()
            audit(session, row, auth["actor"], "outcome_watch_proposed", {"approval_hash": digest})
            return receipt(session, row)

    @app.get("/v1/projects/{project_id}/outcome-watches", response_model=WatchPage)
    def watches(
        project_id: UUID,
        environment_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            environment(session, project_id, environment_id)
            query = select(OutcomeWatch).where(
                OutcomeWatch.project_id == project_id, OutcomeWatch.environment_id == environment_id
            )
            if after:
                query = query.where(OutcomeWatch.id > after)
            rows = session.scalars(query.order_by(OutcomeWatch.id).limit(limit + 1)).all()
            return WatchPage(
                items=[receipt(session, row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/outcome-watches/{watch_id}", response_model=WatchReceipt)
    def read(project_id: UUID, watch_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return receipt(session, load(session, project_id, watch_id))

    @app.post(
        "/v1/projects/{project_id}/outcome-watches/{watch_id}/approve", response_model=WatchReceipt
    )
    def approve(project_id: UUID, watch_id: UUID, body: WatchApproval, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        now = datetime.now(UTC)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = load(session, project_id, watch_id)
            validate_approval(session, row, body.approval_hash, now)
            row.approved_by, row.approval_ref, row.approved_at = (
                auth["actor"],
                body.approval_ref,
                now,
            )
            row.status, row.enabled, row.updated_at = "APPROVED", True, now
            audit(
                session,
                row,
                auth["actor"],
                "outcome_watch_approved",
                {
                    "approval_hash": row.approval_hash,
                    "remaining_windows": row.specification["policy"]["max_windows"]
                    - row.windows_processed,
                },
            )
            return receipt(session, row)

    @app.post(
        "/v1/projects/{project_id}/outcome-watches/{watch_id}/state", response_model=WatchReceipt
    )
    def transition(
        project_id: UUID, watch_id: UUID, body: WatchTransition, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        now = datetime.now(UTC)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = load(session, project_id, watch_id)
            if body.approval_hash != row.approval_hash:
                raise HTTPException(409, "OUTCOME_WATCH_HASH_CHANGED")
            if body.enabled:
                validate_approval(session, row, body.approval_hash, now)
                if principal_role(session, project_id, row.approved_by or "") not in {
                    "admin",
                    "project_admin",
                }:
                    raise HTTPException(409, "OUTCOME_WATCH_REQUIRES_CURRENT_EXACT_APPROVAL")
                row.status = "APPROVED"
            elif row.approved_at is not None and row.status == "APPROVED":
                row.status = "PAUSED"
            row.enabled, row.updated_at = body.enabled, now
            audit(
                session,
                row,
                auth["actor"],
                "outcome_watch_state_changed",
                {"enabled": body.enabled},
            )
            return receipt(session, row)

    @app.get(
        "/v1/projects/{project_id}/outcome-watches/{watch_id}/windows",
        response_model=WatchWindowPage,
    )
    def windows(
        project_id: UUID,
        watch_id: UUID,
        after: int | None = Query(None, ge=0),
        limit: int = Query(20, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            row = load(session, project_id, watch_id)
            query = select(OutcomeWatchWindow).where(
                OutcomeWatchWindow.project_id == project_id,
                OutcomeWatchWindow.environment_id == row.environment_id,
                OutcomeWatchWindow.watch_record_id == row.id,
            )
            if after is not None:
                query = query.where(OutcomeWatchWindow.window_index < after)
            rows = session.scalars(
                query.order_by(OutcomeWatchWindow.window_index.desc()).limit(limit + 1)
            ).all()
            return WatchWindowPage(
                items=[
                    WatchWindowReceipt(
                        id=item.id,
                        evidence_sha256=item.evidence_sha256,
                        evidence=WatchWindowEvidence.model_validate(item.receipt),
                        created_at=item.created_at,
                    )
                    for item in rows[:limit]
                ],
                next_after=rows[limit - 1].window_index if len(rows) > limit else None,
                windows_processed=row.windows_processed,
                retained_windows=receipt(session, row).retained_windows,
            )

    @app.delete("/v1/projects/{project_id}/outcome-watches/{watch_id}")
    def delete(project_id: UUID, watch_id: UUID, confirm_watch_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = load(session, project_id, watch_id)
            if confirm_watch_id != row.watch_id:
                raise HTTPException(409, "CONFIRM_EXACT_OUTCOME_WATCH_REQUEST_ID")
            audit(session, row, auth["actor"], "outcome_watch_deleted")
            session.delete(row)
        return {"deleted": True, "observations_deleted": False, "provider_calls": 0}
