from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    cast,
    column,
    exists,
    func,
    select,
    true,
    values,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from cutback.core.models import EventBatch, IngestReceipt, LLMEvent
from cutback.pricing import Catalog
from cutback.server.db import DailyUsage, RateOverride, StoredEvent


def canonical_attempt(table=StoredEvent):
    """Prefer retained native attempts to an external mirror with exact scoped identity."""
    native = aliased(StoredEvent)
    external = table.event["sampling_policy_id"].as_string() == "external-otel-spans-v1"
    mirror = exists(
        select(native.id).where(
            native.project_id == table.project_id,
            native.environment_id == table.environment_id,
            native.event["trace_id"].as_string() == table.event["trace_id"].as_string(),
            native.event["span_id"].as_string() == table.event["span_id"].as_string(),
            func.coalesce(native.event["sampling_policy_id"].as_string(), "")
            != "external-otel-spans-v1",
        )
    ).correlate(table)
    return ~func.coalesce(external, False) | ~mirror


def event_metadata(table=StoredEvent):
    """Parse each JSON event once in PostgreSQL, rather than once per aggregate expression."""
    fields = {
        "environment": String,
        "traffic_kind": String,
        "call_site_id": String,
        "duration_ms": Float,
        "input_tokens": BigInteger,
        "output_tokens": BigInteger,
        "usage_source": String,
        "logical_request_id": String,
        "status": String,
        "sampling_policy_id": String,
    }
    return (
        func.json_to_record(table.event)
        .table_valued(*(column(name, kind) for name, kind in fields.items()))
        .render_derived(name="event_metadata", with_types=True)
        .lateral()
    )


def usage_columns(table, metadata):
    duration = metadata.c.duration_ms
    input_tokens = metadata.c.input_tokens
    output_tokens = metadata.c.output_tokens
    reported = metadata.c.usage_source == "provider_reported"
    return [
        func.count().label("requests"),
        func.count(
            func.distinct(
                func.coalesce(metadata.c.logical_request_id, cast(table.event_id, String))
            )
        ).label("operations"),
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
        func.count().filter(metadata.c.status == "error").label("errors"),
        func.count()
        .filter(metadata.c.sampling_policy_id == "external-otel-spans-v1")
        .label("external_otel"),
        func.percentile_cont(0.5).within_group(duration).label("p50"),
        func.percentile_cont(0.95).within_group(duration).label("p95"),
        func.max(table.occurred_at).label("latest"),
        func.array_agg(func.distinct(table.price_snapshot_id)).label("snapshots"),
    ]


def render_usage(row):
    requests, priced = row.requests, row.priced_requests
    return {
        "observed_requests": requests,
        "observed_attempts": requests,
        "logical_operations": row.operations,
        "priced_requests": priced,
        "unpriced_requests": requests - priced,
        "modeled_cost_usd": str(row.known_cost) if requests and requests == priced else None,
        "known_modeled_cost_subtotal_usd": str(row.known_cost) if priced else None,
        "input_tokens_observed": int(row.input_tokens) if row.input_tokens is not None else None,
        "output_tokens_observed": int(row.output_tokens) if row.output_tokens is not None else None,
        "input_tokens_counted_or_estimated": int(row.input_tokens_estimated)
        if row.input_tokens_estimated is not None
        else None,
        "output_tokens_counted_or_estimated": int(row.output_tokens_estimated)
        if row.output_tokens_estimated is not None
        else None,
        "non_provider_usage_count": row.non_provider_usage_count,
        "missing_usage_count": row.missing_usage,
        "errors": row.errors,
        "external_otel_observations": row.external_otel,
        "latency_p50_ms": row.p50,
        "latency_p95_ms": row.p95,
        "latest_observation": row.latest.isoformat() if row.latest else None,
        "price_snapshot_ids": row.snapshots or [],
        "actual_billed_cost_usd": None,
        "cost_evidence": "usage_modeled_with_recorded_catalog_not_invoice",
    }


class EventRepository(Protocol):
    def ingest_batch(self, session, batch: EventBatch, environments: dict) -> IngestReceipt: ...
    def query_usage(
        self, session, project_id: UUID, start: datetime, end: datetime, group_by: str
    ) -> dict: ...


class PostgresEventRepository:
    def __init__(self):
        self.catalog = Catalog()

    def ingest_batch(self, session, batch, environments):
        # Preserve first-wins semantics inside a batch, then acquire unique-index
        # conflicts in stable order across concurrent overlapping submissions.
        first: dict[UUID, LLMEvent] = {}
        for event in batch.events:
            first.setdefault(event.event_id, event)
        ordered = sorted(first.values(), key=lambda event: event.event_id.int)
        context = (
            values(
                column("ordinal", Integer),
                column("provider", String),
                column("model", String),
                column("occurred_at", DateTime(timezone=True)),
                column("region", String),
                column("service_tier", String),
            )
            .data(
                [
                    (
                        index,
                        event.provider,
                        event.response_model or event.requested_model,
                        event.occurred_at,
                        event.region,
                        event.service_tier,
                    )
                    for index, event in enumerate(ordered)
                ]
            )
            .alias("event_rate_context")
        )
        # A correlated LIMIT returns at most one matching override per event,
        # without fetching unbounded project history or one round trip per row.
        best = (
            select(
                RateOverride.id,
                RateOverride.rates,
                RateOverride.valid_from,
            )
            .where(
                RateOverride.project_id == batch.project_id,
                RateOverride.provider == context.c.provider,
                RateOverride.model == context.c.model,
                RateOverride.region.is_not_distinct_from(context.c.region),
                RateOverride.service_tier.is_not_distinct_from(context.c.service_tier),
                RateOverride.valid_from <= context.c.occurred_at,
                RateOverride.valid_to.is_(None) | (RateOverride.valid_to > context.c.occurred_at),
            )
            .order_by(RateOverride.valid_from.desc())
            .limit(1)
            .correlate(context)
            .lateral("best_rate")
        )
        rates = {
            row.ordinal: row
            for row in session.execute(
                select(
                    context.c.ordinal,
                    best.c.id,
                    best.c.rates,
                    best.c.valid_from,
                ).select_from(context.outerjoin(best, true()))
            )
        }
        records = []
        for index, event in enumerate(ordered):
            match = rates[index]
            override = (
                {
                    **match.rates,
                    "snapshot_id": "project-override:" + str(match.id),
                    "effective_from": match.valid_from.isoformat(),
                }
                if match.id is not None
                else None
            )
            cost = self.catalog.price(event, override)
            if match.id is not None and cost.amount is not None:
                from dataclasses import replace

                cost = replace(cost, status="contract_rate")
            records.append(
                dict(
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
            )
        accepted = len(
            session.scalars(
                insert(StoredEvent)
                .values(records)
                .on_conflict_do_nothing(index_elements=["project_id", "event_id"])
                .returning(StoredEvent.id)
            ).all()
        )
        from cutback.server.billing import meter_ingestion

        meter_ingestion(session, batch.project_id, accepted)
        session.commit()  # Acknowledge only after durable commit; entire batch is atomic.
        return IngestReceipt(
            accepted=accepted, duplicate=len(batch.events) - accepted, request_id=uuid4()
        )

    def query_usage(
        self, session, project_id, start, end, group_by, *, traffic_kind=None, call_site=None
    ):
        if not session.in_transaction():
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        table = StoredEvent
        metadata = event_metadata(table)
        group = {
            "model": table.model_actual,
            "provider": table.provider,
            "workflow": func.coalesce(table.workflow_id, "workflow_unattributed"),
            "environment": metadata.c.environment,
            "day": func.date_trunc("day", table.occurred_at),
            "call_site": func.coalesce(metadata.c.call_site_id, "unattributed"),
            "traffic": func.coalesce(metadata.c.traffic_kind, "unknown"),
        }[group_by]
        selected = usage_columns(table, metadata)
        scope = [
            canonical_attempt(table),
            table.project_id == project_id,
            table.occurred_at >= start,
            table.occurred_at < end,
        ]
        if traffic_kind:
            scope.append(func.coalesce(metadata.c.traffic_kind, "unknown") == traffic_kind)
        if call_site:
            scope.append(func.coalesce(metadata.c.call_site_id, "unattributed") == call_site)

        total = session.execute(
            select(*selected).select_from(table).join(metadata, true()).where(*scope)
        ).one()
        groups = session.execute(
            select(group.label("key"), *selected)
            .select_from(table)
            .join(metadata, true())
            .where(*scope)
            .group_by(group)
            .order_by(func.sum(table.cost_usd).desc().nullslast(), func.count().desc())
            .limit(101)
        ).all()
        unknown_reasons = session.execute(
            select(table.cost_reason, func.count())
            .select_from(table)
            .join(metadata, true())
            .where(*scope, table.cost_usd.is_(None))
            .group_by(table.cost_reason)
        ).all()
        result = {
            "schema_version": "1.0",
            "project_id": str(project_id),
            "period": {"from": start.isoformat(), "to": end.isoformat()},
            "summary": render_usage(total),
            "group_by": group_by,
            "traffic_kind": traffic_kind or "all",
            "call_site": call_site,
            "groups": [
                {
                    "key": str(row.key) if row.key is not None else "unknown_model",
                    **render_usage(row),
                }
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
        archived = session.scalars(
            select(DailyUsage)
            .where(
                DailyUsage.project_id == project_id,
                DailyUsage.day >= start.replace(hour=0, minute=0, second=0, microsecond=0),
                DailyUsage.day < end,
            )
            .limit(50001)
        ).all()
        if len(archived) > 50000:
            raise ValueError("REPORT_TOO_LARGE: narrow reporting period")
        by_key = {group["key"]: group for group in result["groups"]}
        partial = []
        for row in archived:
            if (
                traffic_kind
                and row.traffic_kind != traffic_kind
                or call_site
                and row.call_site != call_site
            ):
                continue
            if row.day < start or row.day + timedelta(days=1) > end:
                partial.append(row.day.isoformat())
                continue  # Daily retention cannot reconstruct partial-day totals.
            merge_usage(result["summary"], row.report)
            key = {
                "model": row.model,
                "provider": row.provider,
                "workflow": row.workflow,
                "environment": row.environment,
                "day": str(row.day),
                "call_site": row.call_site,
                "traffic": row.traffic_kind,
            }[group_by]
            if key not in by_key:
                by_key[key] = {
                    "key": key,
                    **row.report,
                    "actual_billed_cost_usd": None,
                    "cost_evidence": "retained_daily_modeled_cost",
                }
            else:
                merge_usage(by_key[key], row.report)
        groups_merged = sorted(
            by_key.values(),
            key=lambda g: (
                Decimal(g["known_modeled_cost_subtotal_usd"] or "0"),
                g["observed_attempts"],
            ),
            reverse=True,
        )
        result["groups"] = groups_merged[:100]
        result["groups_truncated"] |= len(groups_merged) > 100
        result["retention"] = {
            "archived_daily_rows": len(archived),
            "partial_days_excluded": sorted(set(partial)),
            "partial_day_totals_complete": not partial,
        }
        if partial:
            result["summary"]["modeled_cost_usd"] = None
        result["coverage"]["external_span_semantics"] = (
            "external spans may aggregate retries; retained native attempts take precedence over "
            "external mirrors only at exact project/environment/trace/span identity; "
            "legacy retained daily summaries cannot be reconciled after raw identities expire"
        )
        return result


def merge_usage(target: dict, source: dict):
    """Sum additive facts; do not average quantiles or invent cross-day operation identities."""
    old_count = target["observed_attempts"]
    counts = (
        "observed_requests",
        "observed_attempts",
        "priced_requests",
        "unpriced_requests",
        "non_provider_usage_count",
        "missing_usage_count",
        "errors",
        "external_otel_observations",
    )
    tokens = (
        "input_tokens_observed",
        "output_tokens_observed",
        "input_tokens_counted_or_estimated",
        "output_tokens_counted_or_estimated",
    )
    for field in (*counts, *tokens):
        values = [v for v in (target.get(field), source.get(field)) if v is not None]
        target[field] = sum(values) if values else None
    known = [
        Decimal(v)
        for v in (
            target.get("known_modeled_cost_subtotal_usd"),
            source.get("known_modeled_cost_subtotal_usd"),
        )
        if v is not None
    ]
    target["known_modeled_cost_subtotal_usd"] = str(sum(known)) if known else None
    target["modeled_cost_usd"] = (
        target["known_modeled_cost_subtotal_usd"] if not target["unpriced_requests"] else None
    )
    for field in ("latency_p50_ms", "latency_p95_ms", "logical_operations"):
        target[field] = None if old_count else source.get(field)
    if old_count:
        target["non_additive_metrics_reason"] = "cannot_reconstruct_from_retained_daily_summaries"
    target["price_snapshot_ids"] = sorted(
        set(target.get("price_snapshot_ids", [])) | set(source.get("price_snapshot_ids", []))
    )
    target["latest_observation"] = max(
        filter(None, (target.get("latest_observation"), source.get("latest_observation"))),
        default=None,
    )
