"""Scoped, paginated content-free episode and native call receipts."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select, true

from cutback.core.models import StrictModel
from cutback.server.db import AuditEvent, ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord


class CallReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    episode_id: UUID | None
    provider_batch_id: UUID | None = None
    sequence: int
    model_key: str
    provider: str
    model: str
    purpose: str
    state: str
    price_sha256: str
    reserved_usd: str
    known_spent_usd: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: float | None
    complete: bool | None
    created_at: datetime
    completed_at: datetime | None


class CallPage(StrictModel):
    items: list[CallReceipt]
    next_after: int | None
    requests_reserved: int
    known_cost_requests: int | None
    known_cost_subtotal_usd: str
    cost_coverage: float | None
    retained_receipts: int
    ledger_version: int | None
    coverage_scope: str = "cumulative_run_totals_receipts_may_be_pruned"


class EpisodeReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    model_key: str
    repeat_index: int
    execution_version: str
    max_requests: int
    state: str
    outcome: str | None
    requests_used: int
    known_cost_requests: int
    reserved_usd: str
    known_spent_usd: str
    summary: dict
    created_at: datetime
    completed_at: datetime | None


class EpisodePage(StrictModel):
    items: list[EpisodeReceipt]
    next_after: UUID | None
    retained_episodes: int
    evidence_scope: str = "declared_synthetic_contract_not_production_state"


class ToolSessionReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    episode_id: UUID
    model_key: str | None = None
    repeat_index: int | None = None
    principal_id: UUID
    broker_id: UUID
    broker_generation: UUID
    policy_sha256: str
    approval_hash: str
    image_id: str
    max_calls: int
    calls_used: int
    max_wall_seconds: int
    state: str
    cleanup_confirmed: bool
    cleanup_requested: bool
    cleanup_attempts: int
    created_at: datetime
    lease_until: datetime
    completed_at: datetime | None


class ToolSessionPage(StrictModel):
    items: list[ToolSessionReceipt]
    next_after: UUID | None
    retained_sessions: int
    tool_cost_usd: None = None
    complete_task_cost_usd: None = None
    evidence_scope: str = "isolated_record_tools_not_production_application_state"


class ToolOperationReceipt(StrictModel):
    id: UUID
    session_id: UUID
    sequence: int
    tool_kind: str
    state: str
    state_changed: bool | None
    created_at: datetime
    completed_at: datetime | None


class ToolOperationPage(StrictModel):
    items: list[ToolOperationReceipt]
    next_after: int | None
    retained_operations: int
    tool_cost_usd: None = None


class ToolContainmentRequest(StrictModel):
    approval_hash: str


def install_execution(app, sessions, authorize, scope):
    def scoped_run(session, project_id, run_id):
        run = session.get(ExperimentRunRecord, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(404, "RUN_NOT_FOUND")
        return run

    @app.get(
        "/v1/projects/{project_id}/experiments/{run_id}/tool-sessions",
        response_model=ToolSessionPage,
    )
    def tool_sessions(
        project_id: UUID,
        run_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped_run(session, project_id, run_id)
            predicates = (
                ToolSessionRecord.project_id == project_id,
                ToolSessionRecord.run_id == run_id,
            )
            count = session.scalar(
                select(func.count()).select_from(ToolSessionRecord).where(*predicates)
            )
            rows = session.execute(
                select(ToolSessionRecord, ExecutionEpisode.model_key, ExecutionEpisode.repeat_index)
                .join(ExecutionEpisode, ExecutionEpisode.id == ToolSessionRecord.episode_id)
                .where(*predicates, ToolSessionRecord.id > after if after else true())
                .order_by(ToolSessionRecord.id)
                .limit(limit + 1)
            ).all()
            items = [
                ToolSessionReceipt.model_validate(row, from_attributes=True).model_copy(
                    update={"model_key": model_key, "repeat_index": repeat_index}
                )
                for row, model_key, repeat_index in rows[:limit]
            ]
            return ToolSessionPage(
                items=items,
                retained_sessions=count,
                next_after=items[-1].id if len(rows) > limit else None,
            )

    @app.get(
        "/v1/projects/{project_id}/experiments/{run_id}/tool-sessions/{session_id}/operations",
        response_model=ToolOperationPage,
    )
    def tool_operations(
        project_id: UUID,
        run_id: UUID,
        session_id: UUID,
        after: int = Query(0, ge=0, le=100),
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped_run(session, project_id, run_id)
            row = session.get(ToolSessionRecord, session_id)
            if not row or row.project_id != project_id or row.run_id != run_id:
                raise HTTPException(404, "ISOLATED_TOOL_SESSION_NOT_FOUND")
            predicates = (
                ToolOperationRecord.project_id == project_id,
                ToolOperationRecord.run_id == run_id,
                ToolOperationRecord.session_id == session_id,
            )
            count = session.scalar(
                select(func.count()).select_from(ToolOperationRecord).where(*predicates)
            )
            rows = session.scalars(
                select(ToolOperationRecord)
                .where(*predicates, ToolOperationRecord.sequence > after)
                .order_by(ToolOperationRecord.sequence)
                .limit(limit + 1)
            ).all()
            items = [
                ToolOperationReceipt.model_validate(row, from_attributes=True)
                for row in rows[:limit]
            ]
            return ToolOperationPage(
                items=items,
                retained_operations=count,
                next_after=items[-1].sequence if len(rows) > limit else None,
            )

    @app.post(
        "/v1/projects/{project_id}/experiments/{run_id}/tool-sessions/{session_id}/containment",
        response_model=ToolSessionReceipt,
    )
    def queue_tool_containment(
        project_id: UUID,
        run_id: UUID,
        session_id: UUID,
        body: ToolContainmentRequest,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            scoped_run(session, project_id, run_id)
            row = session.get(ToolSessionRecord, session_id, with_for_update=True)
            if not row or row.project_id != project_id or row.run_id != run_id:
                raise HTTPException(404, "ISOLATED_TOOL_SESSION_NOT_FOUND")
            if body.approval_hash != row.approval_hash:
                raise HTTPException(409, "ISOLATED_TOOL_CONTAINMENT_APPROVAL_CHANGED")
            if not row.cleanup_confirmed:
                row.cleanup_requested = True
                if not row.cleanup_lease_until or row.cleanup_lease_until <= datetime.now(UTC):
                    row.cleanup_attempts = 0
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="tool_containment_requested",
                        details={"session_id": str(row.id), "run_id": str(run_id)},
                    )
                )
            return ToolSessionReceipt.model_validate(row, from_attributes=True)

    @app.get("/v1/projects/{project_id}/experiments/{run_id}/calls", response_model=CallPage)
    def calls(
        project_id: UUID,
        run_id: UUID,
        after: int = Query(0, ge=0, le=10000),
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            run = scoped_run(session, project_id, run_id)
            filters = [ExecutionCall.project_id == project_id, ExecutionCall.run_id == run_id]
            rows = session.scalars(
                select(ExecutionCall)
                .where(*filters, ExecutionCall.sequence > after)
                .order_by(ExecutionCall.sequence)
                .limit(limit + 1)
            ).all()
            total = session.scalar(select(func.count()).select_from(ExecutionCall).where(*filters))
            items = []
            for row in rows[:limit]:
                fields = {
                    key: getattr(row, key)
                    for key in CallReceipt.model_fields
                    if key not in {"reserved_usd", "known_spent_usd"}
                }
                items.append(
                    CallReceipt(
                        **fields,
                        reserved_usd=str(row.reserved_usd),
                        known_spent_usd=str(row.spent_usd) if row.spent_usd is not None else None,
                    )
                )
            return CallPage(
                items=items,
                next_after=items[-1].sequence if len(rows) > limit else None,
                requests_reserved=run.requests_used,
                known_cost_requests=run.known_cost_requests,
                known_cost_subtotal_usd=str(run.spent_usd),
                retained_receipts=total,
                cost_coverage=run.known_cost_requests / run.requests_used
                if run.known_cost_requests is not None and run.requests_used
                else None,
                ledger_version=run.call_ledger_version,
            )

    @app.get("/v1/projects/{project_id}/experiments/{run_id}/episodes", response_model=EpisodePage)
    def episodes(
        project_id: UUID,
        run_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped_run(session, project_id, run_id)
            filters = [ExecutionEpisode.project_id == project_id, ExecutionEpisode.run_id == run_id]
            rows = session.scalars(
                select(ExecutionEpisode)
                .where(*filters, ExecutionEpisode.id > after if after else true())
                .order_by(ExecutionEpisode.id)
                .limit(limit + 1)
            ).all()
            total = session.scalar(
                select(func.count()).select_from(ExecutionEpisode).where(*filters)
            )
            items = []
            for row in rows[:limit]:
                fields = {
                    key: getattr(row, key)
                    for key in EpisodeReceipt.model_fields
                    if key not in {"reserved_usd", "known_spent_usd"}
                }
                items.append(
                    EpisodeReceipt(
                        **fields,
                        reserved_usd=str(row.reserved_usd),
                        known_spent_usd=str(row.spent_usd),
                    )
                )
            return EpisodePage(
                items=items,
                next_after=items[-1].id if len(rows) > limit else None,
                retained_episodes=total,
            )
