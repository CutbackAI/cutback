"""Expiry revokes authority without replay; unresolved billing keeps its parent ledger."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select

from cutback.server.dataset_lineage import try_lock_lineage
from cutback.server.db import AuditEvent, ExperimentRunRecord
from cutback.server.policy_program_db import (
    SourcePolicyProgramPayload,
    SourcePolicyProgramRecord,
    SourcePolicyProgramRun,
    SourcePolicyProgramSelection,
)
from cutback.server.policy_program_service import ACTIVE


def expire_programs(sessions, *, now=None, only_program_id=None):
    from cutback.server.policy_program_worker import fail_parent

    now = now or datetime.now(UTC)
    statuses = {"READY_FOR_APPROVAL", *ACTIVE}
    with sessions() as reader:
        query = select(SourcePolicyProgramRecord.id, SourcePolicyProgramRecord.project_id).where(
            SourcePolicyProgramRecord.status.in_(statuses),
            or_(
                SourcePolicyProgramRecord.expires_at <= now,
                SourcePolicyProgramRecord.deadline_at <= now,
            ),
        )
        if only_program_id:
            query = query.where(SourcePolicyProgramRecord.id == UUID(str(only_program_id)))
        ranked = query.add_columns(
            SourcePolicyProgramRecord.expires_at.label("expires"),
            func.row_number()
            .over(
                partition_by=SourcePolicyProgramRecord.project_id,
                order_by=(SourcePolicyProgramRecord.expires_at, SourcePolicyProgramRecord.id),
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
            parent = session.get(SourcePolicyProgramRecord, identifier, with_for_update=True)
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
                    "POLICY_PROGRAM_WALL_OR_CONTENT_EXPIRED_NO_REPLAY",
                    status="EXPIRED",
                )
                session.add(
                    AuditEvent(
                        project_id=project,
                        actor="retention_worker",
                        action="source_policy_program_expired",
                        details={"program_id": str(identifier)},
                    )
                )
                count += 1
    return {"expired": count, "provider_calls": 0}


def retain_programs(session, cutoff, selected):
    active_child = (
        select(ExperimentRunRecord.id)
        .join(SourcePolicyProgramRun, SourcePolicyProgramRun.run_id == ExperimentRunRecord.id)
        .where(
            SourcePolicyProgramRun.program_id == SourcePolicyProgramRecord.id,
            ExperimentRunRecord.status.in_(["QUEUED", "RUNNING", "BLOCKED"]),
        )
        .exists()
    )
    parents = list(
        session.scalars(
            select(SourcePolicyProgramRecord.id)
            .where(
                selected(SourcePolicyProgramRecord.project_id),
                SourcePolicyProgramRecord.status.in_(
                    ["COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"]
                ),
                SourcePolicyProgramRecord.requests_used
                == SourcePolicyProgramRecord.known_cost_requests,
                SourcePolicyProgramRecord.created_at < cutoff,
                or_(
                    SourcePolicyProgramRecord.completed_at.is_(None),
                    SourcePolicyProgramRecord.completed_at < cutoff,
                ),
                ~active_child,
            )
            .order_by(SourcePolicyProgramRecord.created_at, SourcePolicyProgramRecord.id)
            .limit(1000)
        )
    )
    if not parents:
        return
    session.execute(
        delete(SourcePolicyProgramSelection).where(
            SourcePolicyProgramSelection.program_id.in_(parents)
        )
    )
    session.execute(
        delete(SourcePolicyProgramPayload).where(SourcePolicyProgramPayload.program_id.in_(parents))
    )
    session.execute(
        delete(SourcePolicyProgramRun).where(SourcePolicyProgramRun.program_id.in_(parents))
    )
    session.execute(
        delete(SourcePolicyProgramRecord).where(SourcePolicyProgramRecord.id.in_(parents))
    )
