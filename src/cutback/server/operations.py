"""Project operations, durable health, shared quotas and retention aggregation."""

import json
import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import case, delete, func, select, text, true
from sqlalchemy.dialects.postgresql import insert

from cutback.core.health import HealthBatch
from cutback.core.inventory import Inventory
from cutback.core.models import Identifier, StrictModel
from cutback.server.db import (
    AuditEvent,
    DailyUsage,
    Environment,
    Project,
    RateBucket,
    RateOverride,
    ScanRun,
    SDKHealth,
    StoredEvent,
    WorkflowRecord,
)
from cutback.server.repository import canonical_attempt, event_metadata, render_usage, usage_columns


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
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        if apply and not session.scalar(text("SELECT pg_try_advisory_xact_lock(1638366141)")):
            return {"apply": False, "status": "ANOTHER_RETENTION_WORKER_ACTIVE"}
        days = session.execute(
            select(
                StoredEvent.project_id, func.date_trunc("day", StoredEvent.occurred_at).label("day")
            )
            .where(StoredEvent.occurred_at < cutoff, selected(StoredEvent.project_id))
            .group_by(StoredEvent.project_id, "day")
            .order_by("day", StoredEvent.project_id)
            .limit(100)
        ).all()
        count = 0
        prune_scopes = []
        for project, day in days:
            day_scope = [
                StoredEvent.project_id == project,
                StoredEvent.occurred_at >= day,
                StoredEvent.occurred_at < day + timedelta(days=1),
            ]
            if session.scalar(
                select(DailyUsage.id)
                .where(DailyUsage.project_id == project, DailyUsage.day == day)
                .limit(1)
            ):
                raise ValueError("LATE_EVENT_FOR_ARCHIVED_DAY: reconcile before pruning")
            metadata = event_metadata()
            dimensions = [
                metadata.c.environment,
                func.coalesce(StoredEvent.workflow_id, "workflow_unattributed"),
                StoredEvent.provider,
                func.coalesce(StoredEvent.model_actual, "unknown_model"),
                func.coalesce(metadata.c.traffic_kind, "unknown"),
                func.coalesce(metadata.c.call_site_id, "unattributed"),
            ]
            groups = session.execute(
                select(
                    *(value.label("dimension_" + str(i)) for i, value in enumerate(dimensions)),
                    *usage_columns(StoredEvent, metadata),
                )
                .select_from(StoredEvent)
                .join(metadata, true())
                .where(*day_scope, canonical_attempt())
                .group_by(*dimensions)
                .limit(10001)
            ).all()
            if len(groups) > 10000:
                raise ValueError("DAY_EXCEEDS_DIMENSION_BOUND: partition before pruning")
            for group in groups:
                key = tuple(group[:6])
                identity = uuid5(NAMESPACE_URL, json.dumps([str(project), day.isoformat(), *key]))
                report = render_usage(group)
                report["population_coverage_percent"] = None
                if apply:
                    session.execute(
                        insert(DailyUsage).values(
                            id=identity,
                            project_id=project,
                            day=day,
                            environment=key[0],
                            workflow=key[1],
                            provider=key[2],
                            model=key[3],
                            traffic_kind=key[4],
                            call_site=key[5],
                            report=report,
                        )
                    )
                count += group.requests
            if apply:
                prune_scopes.append(day_scope)
        if apply:
            # Keep exact native identities visible until every day's mirror selection is frozen.
            # Deleting an earlier native day first would make a later mirror appear canonical.
            for day_scope in prune_scopes:
                session.execute(delete(StoredEvent).where(*day_scope))
            from cutback.server.traces import prune_traces

            prune_traces(session, cutoff, selected)
            from cutback.server.task_economics import retain_task_outcomes

            retain_task_outcomes(session, cutoff, selected)
            from cutback.server.campaign_retention import retain_campaigns

            retain_campaigns(session, now - timedelta(days=aggregate_days), selected)
            from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
            from cutback.server.quality_db import QualityEvaluation

            # Aggregate counters survive receipt pruning; never reconstruct old usage
            # coverage or task latency distributions from the remaining page.
            session.execute(
                delete(ExecutionCall).where(
                    ExecutionCall.completed_at < cutoff,
                    ExecutionCall.state != "reserved",
                    selected(ExecutionCall.project_id),
                )
            )
            session.execute(
                delete(ExecutionEpisode).where(
                    ExecutionEpisode.completed_at < cutoff,
                    ExecutionEpisode.state != "running",
                    selected(ExecutionEpisode.project_id),
                    ~select(ExecutionCall.id)
                    .where(ExecutionCall.episode_id == ExecutionEpisode.id)
                    .exists(),
                )
            )

            # Keep durable rule totals after receipt retention. Never prune queued or
            # leased work: a charged interrupted lease still needs an uncertainty receipt.
            session.execute(
                delete(QualityEvaluation).where(
                    QualityEvaluation.completed_at < cutoff,
                    QualityEvaluation.state.not_in(["queued", "leased"]),
                    selected(QualityEvaluation.project_id),
                )
            )
            from cutback.server.monitoring_db import AlertWindow, Incident, OnlineEvaluation

            session.execute(
                delete(AlertWindow).where(
                    AlertWindow.window_end < cutoff, selected(AlertWindow.project_id)
                )
            )

            session.execute(
                delete(OnlineEvaluation).where(
                    OnlineEvaluation.occurred_at < cutoff, selected(OnlineEvaluation.project_id)
                )
            )
            session.execute(
                delete(Incident).where(
                    Incident.created_at < now - timedelta(days=aggregate_days),
                    Incident.state == "resolved",
                    selected(Incident.project_id),
                )
            )
            from cutback.server.prompt_db import PromptObservation

            session.execute(
                delete(PromptObservation).where(
                    PromptObservation.received_at < cutoff, selected(PromptObservation.project_id)
                )
            )
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

    results: list[dict] = []
    failures: list[dict] = []
    configured = 0
    with sessions() as session:
        projects = session.execute(
            select(Project.id, ProjectSettings.raw_days, ProjectSettings.aggregate_days)
            .outerjoin(ProjectSettings, ProjectSettings.project_id == Project.id)
            .order_by(Project.id)
        ).yield_per(100)
        for project, raw, aggregate in projects:
            scoped = {**options, "project_id": project}
            if raw is not None:
                configured += 1
                scoped.update(raw_days=raw, aggregate_days=aggregate)
            try:
                result = maintain(sessions, **scoped)
                if len(results) < 1000:
                    results.append(result)
            except Exception:
                # One project's poisoned day cannot prevent another project's retention.
                if len(failures) < 100:
                    failures.append(
                        {"project_id": str(project), "code": "RETENTION_PROJECT_FAILED"}
                    )
    return {
        "projects_with_custom_retention": configured,
        "results": results,
        "failures": failures,
        "details_bounded": True,
    }


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
    def inventory(
        project_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        if (
            not start.tzinfo
            or not end.tzinfo
            or not timedelta(0) < end - start <= timedelta(days=395)
        ):
            raise HTTPException(422, "INVALID_TIME_RANGE")
        with sessions() as session:
            row = session.scalar(
                select(ScanRun)
                .where(ScanRun.project_id == project_id)
                .order_by(ScanRun.created_at.desc())
                .limit(1)
            )
            if not row:
                return {"scan_id": None, "inventory": None, "status": "NO_SCAN"}
            report = repository.query_usage(
                session, project_id, start, end, "call_site", traffic_kind="application"
            )
            runtime = {group["key"]: group for group in report["groups"]}
            known = {site["call_site_id"] for site in row.inventory["call_sites"]}
            return {
                "scan_id": str(row.id),
                "inventory": row.inventory,
                "runtime_by_call_site": runtime,
                "runtime_period": report["period"],
                "runtime_groups_truncated": report["groups_truncated"],
                "unmatched_observed_attempts": sum(
                    item["observed_attempts"] for key, item in runtime.items() if key not in known
                ),
                "source_binding": "reported_call_site_identity_not_exhaustive_coverage",
            }

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
            report = repository.query_usage(
                session, project_id, start, end, "workflow", traffic_kind="application"
            )
            result = observed_opportunities(report)
            from cutback.core.inventory import workflow_identity
            from cutback.server.db import ScanRun

            latest = session.scalar(
                select(ScanRun)
                .where(ScanRun.project_id == project_id)
                .order_by(ScanRun.created_at.desc())
                .limit(1)
            )
            from cutback.server.workflow_labels import workflow_labels

            labels = workflow_labels(session, project_id)
            if latest:
                for site in latest.inventory["call_sites"]:
                    for entry in site["entry_points"]:
                        key, symbol = workflow_identity(entry["file"], entry["symbol"])
                        labels[key] = f"{entry['file']} · {symbol}"
            for opportunity in result["opportunities"]:
                opportunity["label"] = labels.get(opportunity["target"], opportunity["target"])
            result["traffic_kind"] = "application"
            result["overhead"] = {
                kind: repository.query_usage(
                    session, project_id, start, end, "workflow", traffic_kind=kind
                )["summary"]
                for kind in ("evaluation", "judge", "unknown")
            }
            return result
