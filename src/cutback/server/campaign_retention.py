"""Expire execution authority independently of inference; prune bounded historical metadata."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, or_, select, true

from cutback.server.campaign_db import (
    CampaignCandidate,
    CampaignEconomicReference,
    CampaignRecord,
    CampaignRun,
    CampaignSelection,
)
from cutback.server.campaign_ledger import ACTIVE
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent


def expire_campaigns(sessions, *, now=None, only_campaign_id=None):
    from cutback.server.campaign_worker import fail_parent

    now = now or datetime.now(UTC)
    identifier = UUID(str(only_campaign_id)) if only_campaign_id is not None else None
    statuses = {"QUEUED", "READY_FOR_APPROVAL", *ACTIVE}
    with sessions() as reader:
        expired = reader.execute(
            select(CampaignRecord.id, CampaignRecord.project_id)
            .where(
                CampaignRecord.status.in_(statuses),
                CampaignRecord.id == identifier if identifier is not None else true(),
                or_(CampaignRecord.expires_at <= now, CampaignRecord.deadline_at <= now),
            )
            .order_by(CampaignRecord.expires_at, CampaignRecord.id)
            .limit(100)
        ).all()
    count = 0
    for identifier, project in expired:
        with sessions.begin() as session:
            lock_lineage(session, project)
            row = session.get(CampaignRecord, identifier, with_for_update=True)
            if (
                row
                and row.status in statuses
                and (
                    row.expires_at <= now or row.deadline_at is not None and row.deadline_at <= now
                )
            ):
                fail_parent(session, row, "CAMPAIGN_WALL_OR_CONTENT_AUTHORITY_EXPIRED_NO_REPLAY")
                row.lease_token = row.lease_until = None
                session.add(
                    AuditEvent(
                        project_id=project,
                        actor="retention_worker",
                        action="campaign_expired",
                        details={"campaign_id": str(row.id)},
                    )
                )
                count += 1
    return {"expired": count, "provider_calls": 0}


def retain_campaigns(session, cutoff, selected):
    parents = (
        select(CampaignRecord.id)
        .where(
            selected(CampaignRecord.project_id),
            CampaignRecord.status.in_(["COMPLETED", "FAILED", "CANCELLED", "ABSTAINED"]),
            CampaignRecord.created_at < cutoff,
            or_(CampaignRecord.completed_at.is_(None), CampaignRecord.completed_at < cutoff),
        )
        .order_by(CampaignRecord.created_at, CampaignRecord.id)
        .limit(1000)
    )
    for cls in (CampaignSelection, CampaignCandidate, CampaignRun, CampaignEconomicReference):
        session.execute(delete(cls).where(cls.campaign_id.in_(parents)))
    session.execute(delete(CampaignRecord).where(CampaignRecord.id.in_(parents)))
