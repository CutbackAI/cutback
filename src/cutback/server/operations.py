"""Project operations, durable health, shared quotas and retention aggregation."""

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import case, delete, select, text
from sqlalchemy.dialects.postgresql import insert

from cutback.core.health import HealthBatch
from cutback.core.inventory import Inventory
from cutback.core.models import Identifier, StrictModel
from cutback.server.db import (
    AuditEvent,
    DailyUsage,
    Environment,
    RateBucket,
    RateOverride,
    ScanRun,
    SDKHealth,
    StoredEvent,
    WorkflowRecord,
)


def enforce_quota(sessions, key: str, limit: int):
    window = int(datetime.now(UTC).timestamp() // 60)
    with sessions.begin() as session:
        count = session.scalar(
            insert(RateBucket)
            .values(key=key, window=window, count=1)
            .on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "window": window,
                    "count": case((RateBucket.window == window, RateBucket.count + 1), else_=1),
                },
            )
            .returning(RateBucket.count)
        )
    if count > limit:
        raise HTTPException(429, "RATE_LIMITED")


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def maintain(sessions, *, now=None, raw_days=30, aggregate_days=395, apply=False, project_id=None):
    from cutback.server.enterprise_db import ProjectSettings

    def selected(column):
        return (
            column == project_id
            if project_id
            else column.not_in(select(ProjectSettings.project_id))
        )

    if not 1 <= raw_days <= aggregate_days <= 3650:
        raise ValueError("invalid retention periods")
    now = now or datetime.now(UTC)
    cutoff = (now - timedelta(days=raw_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    with sessions() as session:
        if apply and not session.scalar(text("SELECT pg_try_advisory_xact_lock(1638366141)")):
            return {"apply": False, "status": "ANOTHER_RETENTION_WORKER_ACTIVE"}
        rows = session.scalars(
            select(StoredEvent)
            .where(StoredEvent.occurred_at < cutoff, selected(StoredEvent.project_id))
            .order_by(StoredEvent.occurred_at)
            .limit(10000)
        ).all()
        # Retain complete days as one idempotent aggregate, never a partial overwrite.
        days = {
            (row.project_id, row.occurred_at.replace(hour=0, minute=0, second=0, microsecond=0))
            for row in rows
        }
        count = 0
        for project, day in days:
            complete = session.scalars(
                select(StoredEvent)
                .where(
                    StoredEvent.project_id == project,
                    StoredEvent.occurred_at >= day,
                    StoredEvent.occurred_at < day + timedelta(days=1),
                )
                .limit(100001)
            ).all()
            if len(complete) > 100000:
                raise ValueError("DAY_EXCEEDS_RETENTION_BATCH_LIMIT: partition before pruning")
            grouped: dict[tuple, list] = {}
            for row in complete:
                key = (
                    row.event["environment"],
                    row.workflow_id or "workflow_unattributed",
                    row.provider,
                    row.model_actual or "unknown_model",
                )
                grouped.setdefault(key, []).append(row)
            for key, group in grouped.items():
                known = [row.cost_usd for row in group if row.cost_usd is not None]
                identity = uuid5(NAMESPACE_URL, json.dumps([str(project), day.isoformat(), *key]))
                report = {
                    "observed_requests": len(group),
                    "observed_attempts": len(group),
                    "logical_operations": len(
                        {row.event.get("logical_request_id") or str(row.event_id) for row in group}
                    ),
                    "known_modeled_cost_subtotal_usd": str(sum(known, Decimal(0)))
                    if known
                    else None,
                    "unpriced_requests": len(group) - len(known),
                    "priced_requests": len(known),
                    "modeled_cost_usd": str(sum(known, Decimal(0)))
                    if known and len(known) == len(group)
                    else None,
                    "price_snapshot_ids": sorted(
                        {row.price_snapshot_id for row in group if row.price_snapshot_id}
                    ),
                    "input_tokens_observed": _sum_known(
                        row.event.get("input_tokens")
                        for row in group
                        if row.event["usage_source"] == "provider_reported"
                    ),
                    "output_tokens_observed": _sum_known(
                        row.event.get("output_tokens")
                        for row in group
                        if row.event["usage_source"] == "provider_reported"
                    ),
                    "missing_usage_count": sum(
                        row.event.get("input_tokens") is None
                        or row.event.get("output_tokens") is None
                        for row in group
                    ),
                    "errors": sum(row.event["status"] == "error" for row in group),
                    "input_tokens_counted_or_estimated": _sum_known(
                        row.event.get("input_tokens")
                        for row in group
                        if row.event["usage_source"] != "provider_reported"
                    ),
                    "output_tokens_counted_or_estimated": _sum_known(
                        row.event.get("output_tokens")
                        for row in group
                        if row.event["usage_source"] != "provider_reported"
                    ),
                    "non_provider_usage_count": sum(
                        row.event["usage_source"] != "provider_reported" for row in group
                    ),
                    "latest_observation": max(row.occurred_at for row in group).isoformat(),
                    "latency_p50_ms": percentile([row.event["duration_ms"] for row in group], 0.5),
                    "latency_p95_ms": percentile([row.event["duration_ms"] for row in group], 0.95),
                    "population_coverage_percent": None,
                }
                if apply:
                    if session.get(DailyUsage, identity):
                        raise ValueError("LATE_EVENT_FOR_ARCHIVED_DAY: reconcile before pruning")
                    session.execute(
                        insert(DailyUsage).values(
                            id=identity,
                            project_id=project,
                            day=day,
                            environment=key[0],
                            workflow=key[1],
                            provider=key[2],
                            model=key[3],
                            report=report,
                        )
                    )
                    session.execute(
                        delete(StoredEvent).where(StoredEvent.id.in_([row.id for row in group]))
                    )
                count += len(group)
        if apply:
            session.execute(
                delete(DailyUsage).where(
                    DailyUsage.day < now - timedelta(days=aggregate_days),
                    selected(DailyUsage.project_id),
                )
            )
            session.execute(
                delete(RateBucket).where(RateBucket.window < int(now.timestamp() / 60) - 2)
            )
            session.execute(
                delete(SDKHealth).where(
                    SDKHealth.updated_at < cutoff, selected(SDKHealth.project_id)
                )
            )
            session.add(
                AuditEvent(
                    project_id=None,
                    actor="retention_worker",
                    action="retention_applied",
                    details={
                        "raw_events_deleted": count,
                        "raw_days": raw_days,
                        "aggregate_days": aggregate_days,
                    },
                )
            )
            session.commit()
        return {
            "apply": apply,
            "raw_events_eligible": count,
            "days": len(days),
            "raw_retention_days": raw_days,
            "aggregate_retention_days": aggregate_days,
        }


def maintain_all(sessions, **options):
    from cutback.server.enterprise_db import ProjectSettings

    with sessions() as session:
        configured = [
            (row.project_id, row.raw_days, row.aggregate_days)
            for row in session.scalars(select(ProjectSettings))
        ]
    results = [maintain(sessions, **options)]
    for project, raw, aggregate in configured:
        results.append(
            maintain(
                sessions,
                **{**options, "raw_days": raw, "aggregate_days": aggregate},
                project_id=project,
            )
        )
    return {"projects_with_custom_retention": len(configured), "results": results}


def _sum_known(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


class OverrideInput(StrictModel):
    provider: Literal["openai", "anthropic"]
    model: Identifier
    region: Identifier | None = None
    service_tier: Identifier | None = None
    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    cache_read: Decimal = Field(ge=0)
    cache_write: Decimal | None = Field(default=None, ge=0)
    cache_write_1h: Decimal | None = Field(default=None, ge=0)
    valid_from: datetime
    valid_to: datetime | None = None


def install_operations(app, sessions, authorize, scope, repository):
    @app.post("/v1/ingest/health")
    def health(body: HealthBatch, auth=Depends(authorize)):
        scope(auth, body.project_id, ("ingest",))
        with sessions.begin() as session:
            environment = session.get(Environment, auth["environment_id"])
            if not environment or environment.name != body.environment:
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
            values = {
                "project_id": body.project_id,
                "instance_id": body.instance_id,
                "environment_id": environment.id,
                "updated_at": datetime.now(UTC),
                "counters": {
                    **body.model_dump(mode="json")["counters"],
                    "_service_name": body.service_name,
                    "_sdk_version": body.sdk_version,
                },
            }
            session.execute(
                insert(SDKHealth)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["project_id", "instance_id"],
                    set_={"updated_at": values["updated_at"], "counters": values["counters"]},
                )
            )
        return {"status": "recorded"}

    @app.get("/v1/projects/{project_id}/health")
    def project_health(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.scalars(
                select(SDKHealth)
                .where(SDKHealth.project_id == project_id)
                .order_by(SDKHealth.updated_at.desc())
                .limit(100)
            ).all()
            return {
                "instances": [
                    {
                        "instance_id": str(row.instance_id),
                        "updated_at": row.updated_at.isoformat(),
                        "counters": {
                            k: v for k, v in row.counters.items() if not k.startswith("_")
                        },
                        "service_name": row.counters.get("_service_name"),
                        "sdk_version": row.counters.get("_sdk_version"),
                        "environment_id": str(row.environment_id),
                    }
                    for row in rows
                ],
                "coverage_percent": None,
            }

    @app.post("/v1/projects/{project_id}/scans", status_code=201)
    def upload_scan(project_id: UUID, body: Inventory, auth=Depends(authorize)):
        scope(auth, project_id, ("editor", "admin"))
        inventory = body.model_dump(mode="json")
        inventory["project"]["root"] = "."  # Do not expose developer machine paths to other users.
        with sessions.begin() as session:
            row = ScanRun(
                project_id=project_id, commit_sha=body.project.get("commit"), inventory=inventory
            )
            session.add(row)
            session.flush()
            for site in body.call_sites:
                for entry in site.entry_points:
                    stable = str(entry["symbol"])
                    if not session.scalar(
                        select(WorkflowRecord).where(
                            WorkflowRecord.project_id == project_id,
                            WorkflowRecord.stable_key == stable,
                        )
                    ):
                        session.add(
                            WorkflowRecord(
                                project_id=project_id,
                                stable_key=stable,
                                entry_point=entry,
                                attribution_method="heuristic",
                            )
                        )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="scan_uploaded",
                    details={"scan_id": str(row.id)},
                )
            )
        return {"scan_id": str(row.id), "sites": len(body.call_sites)}

    @app.get("/v1/projects/{project_id}/inventory")
    def inventory(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = session.scalar(
                select(ScanRun)
                .where(ScanRun.project_id == project_id)
                .order_by(ScanRun.created_at.desc())
                .limit(1)
            )
            return (
                {"scan_id": str(row.id), "inventory": row.inventory}
                if row
                else {"scan_id": None, "inventory": None, "status": "NO_SCAN"}
            )

    @app.get("/v1/projects/{project_id}/workflows")
    def workflows(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "stable_key": row.stable_key,
                    "entry_point": row.entry_point,
                    "attribution": row.attribution_method,
                }
                for row in session.scalars(
                    select(WorkflowRecord).where(WorkflowRecord.project_id == project_id).limit(100)
                )
            ]

    @app.get("/v1/projects/{project_id}/daily")
    def daily(
        project_id: UUID,
        limit: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "day": row.day.isoformat(),
                    "provider": row.provider,
                    "model": row.model,
                    "workflow": row.workflow,
                    "environment": row.environment,
                    **row.report,
                }
                for row in session.scalars(
                    select(DailyUsage)
                    .where(DailyUsage.project_id == project_id)
                    .order_by(DailyUsage.day.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.post("/v1/projects/{project_id}/prices/overrides", status_code=201)
    def override(project_id: UUID, body: OverrideInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        if not body.valid_from.tzinfo or (
            body.valid_to and (not body.valid_to.tzinfo or body.valid_to <= body.valid_from)
        ):
            raise HTTPException(422, "INVALID_RATE_PERIOD")
        rates = {
            key: str(value) if value is not None else None
            for key, value in body.model_dump(
                include={"input", "output", "cache_read", "cache_write", "cache_write_1h"}
            ).items()
        }
        with sessions.begin() as session:
            row = RateOverride(
                project_id=project_id,
                provider=body.provider,
                model=body.model,
                region=body.region,
                service_tier=body.service_tier,
                valid_from=body.valid_from,
                valid_to=body.valid_to,
                rates=rates,
                approved_by=auth["actor"],
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="rate_override_created",
                    details={"override_id": str(row.id)},
                )
            )
        return {"id": str(row.id)}

    @app.get("/v1/projects/{project_id}/prices")
    def prices(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return {
                "catalog": repository.catalog.data,
                "overrides": [
                    {
                        "id": str(row.id),
                        "provider": row.provider,
                        "model": row.model,
                        "region": row.region,
                        "service_tier": row.service_tier,
                        "rates": row.rates,
                    }
                    for row in session.scalars(
                        select(RateOverride).where(RateOverride.project_id == project_id)
                    )
                ],
            }

    @app.get("/v1/projects/{project_id}/opportunities")
    def opportunities(
        project_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        from cutback.optimizer import observed_opportunities

        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        if (
            not start.tzinfo
            or not end.tzinfo
            or not timedelta(0) < end - start <= timedelta(days=395)
        ):
            raise HTTPException(422, "INVALID_TIME_RANGE")
        with sessions() as session:
            report = repository.query_usage(session, project_id, start, end, "workflow")
            return observed_opportunities(report)
