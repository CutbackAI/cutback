from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Float, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.sql.elements import ColumnElement

from cutback.core.models import EventBatch, IngestReceipt
from cutback.pricing import Catalog
from cutback.server.db import StoredEvent


class EventRepository(Protocol):
    def ingest_batch(self, session, batch: EventBatch, environments: dict) -> IngestReceipt: ...
    def query_usage(
        self, session, project_id: UUID, start: datetime, end: datetime, group_by: str
    ) -> dict: ...


class PostgresEventRepository:
    def __init__(self):
        self.catalog = Catalog()

    def ingest_batch(self, session, batch, environments):
        accepted = 0
        for event in batch.events:
            cost = self.catalog.price(event)
            statement = (
                insert(StoredEvent)
                .values(
                    id=uuid4(),
                    event_id=event.event_id,
                    project_id=batch.project_id,
                    environment_id=environments[event.environment],
                    occurred_at=event.occurred_at,
                    received_at=datetime.now(UTC),
                    workflow_id=event.workflow_id,
                    provider=event.provider,
                    model_actual=event.response_model or event.requested_model,
                    cost_usd=cost.amount,
                    cost_status=cost.status,
                    cost_reason=cost.reason,
                    price_snapshot_id=cost.snapshot_id,
                    cost_breakdown=cost.breakdown,
                    event=event.model_dump(mode="json"),
                )
                .on_conflict_do_nothing(index_elements=["project_id", "event_id"])
                .returning(StoredEvent.id)
            )
            if session.execute(statement).scalar_one_or_none():
                accepted += 1
        session.commit()  # Acknowledge only after durable commit; entire batch is atomic.
        return IngestReceipt(
            accepted=accepted, duplicate=len(batch.events) - accepted, request_id=uuid4()
        )

    def query_usage(self, session, project_id, start, end, group_by):
        table = StoredEvent
        group = {
            "model": table.model_actual,
            "provider": table.provider,
            "workflow": func.coalesce(table.workflow_id, "workflow_unattributed"),
            "environment": table.event["environment"].as_string(),
            "day": func.date_trunc("day", table.occurred_at),
        }[group_by]
        duration = cast(table.event["duration_ms"].as_string(), Float)
        input_tokens = cast(table.event["input_tokens"].as_string(), BigInteger)
        output_tokens = cast(table.event["output_tokens"].as_string(), BigInteger)
        reported = table.event["usage_source"].as_string() == "provider_reported"
        selected: list[ColumnElement] = [
            func.count().label("requests"),
            func.count(table.cost_usd).label("priced_requests"),
            func.sum(table.cost_usd).label("known_cost"),
            func.sum(input_tokens).filter(reported).label("input_tokens"),
            func.sum(output_tokens).filter(reported).label("output_tokens"),
            func.sum(input_tokens).filter(~reported).label("input_tokens_estimated"),
            func.sum(output_tokens).filter(~reported).label("output_tokens_estimated"),
            func.count().filter(~reported).label("non_provider_usage_count"),
            func.count()
            .filter(input_tokens.is_(None) | output_tokens.is_(None))
            .label("missing_usage"),
            func.count().filter(table.event["status"].as_string() == "error").label("errors"),
            func.percentile_cont(0.5).within_group(duration).label("p50"),
            func.percentile_cont(0.95).within_group(duration).label("p95"),
            func.max(table.occurred_at).label("latest"),
            func.array_agg(func.distinct(table.price_snapshot_id)).label("snapshots"),
        ]
        scope = (
            table.project_id == project_id,
            table.occurred_at >= start,
            table.occurred_at < end,
        )

        def render(row):
            requests, priced = row.requests, row.priced_requests
            return {
                "observed_requests": requests,
                "priced_requests": priced,
                "unpriced_requests": requests - priced,
                "modeled_cost_usd": str(row.known_cost)
                if requests and requests == priced
                else None,
                "known_modeled_cost_subtotal_usd": str(row.known_cost) if priced else None,
                "input_tokens_observed": row.input_tokens,
                "output_tokens_observed": row.output_tokens,
                "input_tokens_counted_or_estimated": row.input_tokens_estimated,
                "output_tokens_counted_or_estimated": row.output_tokens_estimated,
                "non_provider_usage_count": row.non_provider_usage_count,
                "missing_usage_count": row.missing_usage,
                "errors": row.errors,
                "latency_p50_ms": row.p50,
                "latency_p95_ms": row.p95,
                "latest_observation": row.latest.isoformat() if row.latest else None,
                "price_snapshot_ids": row.snapshots or [],
                "actual_billed_cost_usd": None,
                "cost_evidence": "usage_modeled_with_recorded_catalog_not_invoice",
            }

        total = session.execute(select(*selected).where(*scope)).one()
        groups = session.execute(
            select(group.label("key"), *selected)
            .where(*scope)
            .group_by(group)
            .order_by(func.count().desc())
            .limit(101)
        ).all()
        unknown_reasons = session.execute(
            select(table.cost_reason, func.count())
            .where(*scope, table.cost_usd.is_(None))
            .group_by(table.cost_reason)
        ).all()
        return {
            "schema_version": "1.0",
            "project_id": str(project_id),
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "summary": render(total),
            "group_by": group_by,
            "groups": [
                {"key": str(row.key) if row.key is not None else "unknown_model", **render(row)}
                for row in groups[:100]
            ],
            "groups_truncated": len(groups) > 100,
            "unpriced_reasons": {reason or "unknown": count for reason, count in unknown_reasons},
            "coverage": {
                "population_coverage_percent": None,
                "sample_correction": None,
                "reason": "exporter_loss_and_uninstrumented_calls_not_observable_from_ingest",
            },
            "next_action": "validate_coverage_and_collect_authorized_evaluation_evidence",
            "semantic_quality": "NOT VERIFIED",
        }
