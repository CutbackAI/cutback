"""Scoped, paginated content-free episode and native call receipts."""

from datetime import datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select, true

from cutback.core.models import StrictModel
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode


class CallReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    episode_id: UUID | None
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


def install_execution(app, sessions, authorize, scope):
    def scoped_run(session, project_id, run_id):
        run = session.get(ExperimentRunRecord, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(404, "RUN_NOT_FOUND")
        return run

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
