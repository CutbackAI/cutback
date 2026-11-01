"""Expiry revokes authority without replay; unresolved billing keeps its parent ledger."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select

from cutback.server.dataset_lineage import try_lock_lineage
from cutback.server.db import AuditEvent, ExperimentRunRecord
from cutback.server.routing_program_db import (
    RoutingProgramRecord,
    RoutingProgramRun,
    RoutingProgramSelection,
)
from cutback.server.routing_program_service import ACTIVE


def expire_programs(sessions, *, now=None, only_program_id=None):
    from cutback.server.routing_program_worker import fail_parent

    now = now or datetime.now(UTC)
    statuses = {"READY_FOR_APPROVAL", *ACTIVE}
    with sessions() as reader:
        query = select(RoutingProgramRecord.id, RoutingProgramRecord.project_id).where(
            RoutingProgramRecord.status.in_(statuses),
            or_(RoutingProgramRecord.expires_at <= now, RoutingProgramRecord.deadline_at <= now),
        )
        if only_program_id:
            query = query.where(RoutingProgramRecord.id == UUID(str(only_program_id)))
        ranked = query.add_columns(
            RoutingProgramRecord.expires_at.label("expires"),
            func.row_number()
            .over(
                partition_by=RoutingProgramRecord.project_id,
                order_by=(RoutingProgramRecord.expires_at, RoutingProgramRecord.id),
            )
            .label("rank"),
        ).subquery()
        candidates = reader.execute(
            select(ranked.c.id, ranked.c.project_id)
            .where(ranked.c.rank == 1)
            .order_by(ranked.c.expires, ranked.c.id)
            .limit(100)
        ).all()
    count = 0
    for identifier, project in candidates:
        with sessions.begin() as session:
            if not try_lock_lineage(session, project):
                continue
            parent = session.get(RoutingProgramRecord, identifier, with_for_update=True)
            if (
                parent
                and parent.status in statuses
                and (
                    parent.expires_at <= now
                    or parent.deadline_at is not None
                    and parent.deadline_at <= now
                )
            ):
                fail_parent(
                    session,
                    parent,
                    "ROUTING_PROGRAM_WALL_OR_CONTENT_EXPIRED_NO_REPLAY",
                    status="EXPIRED",
                )
                session.add(
                    AuditEvent(
                        project_id=project,
                        actor="retention_worker",
                        action="routing_program_expired",
                        details={"program_id": str(identifier)},
                    )
                )
                count += 1
    return {"expired": count, "provider_calls": 0}


def retain_programs(session, cutoff, selected):
    active_child = (
        select(ExperimentRunRecord.id)
        .join(RoutingProgramRun, RoutingProgramRun.run_id == ExperimentRunRecord.id)
        .where(
            RoutingProgramRun.program_id == RoutingProgramRecord.id,
            ExperimentRunRecord.status.in_(["QUEUED", "RUNNING", "BLOCKED"]),
        )
        .exists()
    )
    parents = list(
        session.scalars(
            select(RoutingProgramRecord.id)
            .where(
                selected(RoutingProgramRecord.project_id),
                RoutingProgramRecord.status.in_(
                    ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"]
                ),
                RoutingProgramRecord.requests_used == RoutingProgramRecord.known_cost_requests,
                RoutingProgramRecord.created_at < cutoff,
                or_(
                    RoutingProgramRecord.completed_at.is_(None),
                    RoutingProgramRecord.completed_at < cutoff,
                ),
                ~active_child,
            )
            .order_by(RoutingProgramRecord.created_at, RoutingProgramRecord.id)
            .limit(1000)
        )
    )
    if not parents:
        return
    session.execute(
        delete(RoutingProgramSelection).where(RoutingProgramSelection.program_id.in_(parents))
    )
    session.execute(delete(RoutingProgramRun).where(RoutingProgramRun.program_id.in_(parents)))
    session.execute(delete(RoutingProgramRecord).where(RoutingProgramRecord.id.in_(parents)))
