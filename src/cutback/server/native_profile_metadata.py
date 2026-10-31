"""Freeze bounded canonical metadata separately from encrypted request fingerprints."""

from datetime import UTC

from sqlalchemy import select

from cutback.core.models import LLMEvent
from cutback.evals.datasets import digest
from cutback.server.db import Environment, StoredEvent


def retained_metadata(session, analysis, *, as_of=True):
    from cutback.server.native_profile_service import _event_clause

    query = select(StoredEvent).join(Environment).where(*_event_clause(analysis)).limit(1001)
    if as_of and analysis.completed_at is not None:
        query = query.where(StoredEvent.received_at <= analysis.completed_at)
    return session.scalars(query).all()


def metadata_digest(rows):
    if len(rows) > 1000:
        raise ValueError("NATIVE_PROFILE_METADATA_SNAPSHOT_BOUND")
    values = []
    for row in sorted(rows, key=lambda item: item.event_id.int):
        if row.cost_usd is not None and (not row.cost_usd.is_finite() or row.cost_usd < 0):
            raise ValueError("NATIVE_PROFILE_METADATA_COST_INVALID")
        values.append(
            {
                "event_id": str(row.event_id),
                "occurred_at": row.occurred_at.astimezone(UTC).isoformat(),
                "received_at": row.received_at.astimezone(UTC).isoformat(),
                "event": LLMEvent.model_validate(row.event).model_dump(mode="json"),
                "provider_modeled_cost_usd": str(row.cost_usd)
                if row.cost_usd is not None
                else None,
                "cost_status": row.cost_status,
                "price_snapshot_id": row.price_snapshot_id,
            }
        )
    return digest({"version": "native-profile-metadata-v1", "events": values})
