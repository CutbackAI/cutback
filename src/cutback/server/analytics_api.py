"""Shared bounded analytical views over canonical observed usage, never arbitrary queries."""

import hashlib
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from cutback.core.request_profiles import fingerprint
from cutback.server.analytics_contracts import (
    AnalyticsData,
    AnalyticsDefinition,
    AnalyticsDetail,
    AnalyticsInput,
    AnalyticsPage,
    AnalyticsPanelData,
    AnalyticsPoint,
    AnalyticsSummary,
    Metric,
    Unit,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, Environment, Project
from cutback.server.monitoring_db import SavedView
from cutback.server.operations import enforce_quota
from cutback.server.repository import PostgresEventRepository

SEMANTICS: dict[Metric, tuple[Unit, str]] = {
    "observed_attempts": (
        "attempts",
        "Canonical retained observed attempts; population coverage is unknown.",
    ),
    "errors": (
        "attempts",
        "Retained attempts marked error; not independently verified task failures.",
    ),
    "known_modeled_cost_subtotal_usd": (
        "USD",
        "Known modeled cost subtotal; excludes unpriced attempts and is not an invoice.",
    ),
    "latency_p95_ms": (
        "ms",
        "Retained duration p95 when available; missing/combined retained quantiles stay unknown.",
    ),
    "input_tokens_observed": (
        "tokens",
        "Provider-reported input-token subtotal; missing or estimated usage is not inferred.",
    ),
    "output_tokens_observed": (
        "tokens",
        "Provider-reported output-token subtotal; missing or estimated usage is not inferred.",
    ),
}


def quota_bucket(project):
    return "analytics:" + hashlib.sha256(str(project).encode()).hexdigest()[:54]


def definition(row):
    try:
        return AnalyticsDefinition.model_validate(row.specification)
    except ValidationError:
        raise HTTPException(409, "ANALYTICS_DEFINITION_UNAVAILABLE") from None


def summary(row):
    value = definition(row)
    return AnalyticsSummary(
        id=row.id,
        key=row.key,
        revision=row.revision,
        environment=value.environment,
        window_days=value.window_days,
        panel_count=len(value.panels),
        definition_sha256=fingerprint(value.model_dump(mode="json")),
    )


def detail(row):
    return AnalyticsDetail(**summary(row).model_dump(), panels=definition(row).panels)


def scoped(session, project, identifier, *, lock=False):
    row = session.get(SavedView, identifier, with_for_update=lock)
    if (
        not row
        or row.project_id != project
        or row.specification.get("page") != "analytics_dashboard"
    ):
        raise HTTPException(404, "ANALYTICS_DASHBOARD_NOT_FOUND")
    return row


def point(key, report, metric, *, complete=True):
    attempts = report.get("observed_attempts", 0)
    amount = report.get(metric) if attempts else None
    if metric == "latency_p95_ms" and not complete:
        amount = None
    return AnalyticsPoint(
        key=key,
        value=Decimal(str(amount)) if amount is not None else None,
        observed_attempts=attempts,
        unpriced_attempts=report.get("unpriced_requests"),
        missing_usage_attempts=report.get("missing_usage_count"),
        timed_attempts=report.get("observed_duration_requests"),
        modeled_cost_usd=report.get("modeled_cost_usd") if complete else None,
        known_modeled_cost_subtotal_usd=report.get("known_modeled_cost_subtotal_usd"),
    )


def panel_data(panel, report, start, end):
    partial = report["retention"]["partial_days_excluded"]
    points = []
    if panel.group_by == "day":
        rows = {
            datetime.fromisoformat(row["key"]).astimezone(UTC).date().isoformat(): row
            for row in report["groups"]
        }
        day = start
        while day < end:
            key = day.date().isoformat()
            value = rows.get(key, {})
            complete = all(
                datetime.fromisoformat(item).astimezone(UTC).date() != day.date()
                for item in partial
            )
            points.append(point(key, value, panel.metric, complete=complete))
            day += timedelta(days=1)
    else:
        points = [
            point(row["key"], row, panel.metric, complete=not partial) for row in report["groups"]
        ]
    unit, semantics = SEMANTICS[panel.metric]
    return AnalyticsPanelData(
        key=panel.key,
        metric=panel.metric,
        chart=panel.chart,
        group_by=panel.group_by,
        unit=unit,
        metric_semantics=semantics,
        summary=point("total", report["summary"], panel.metric, complete=not partial),
        points=points,
        points_truncated=report["groups_truncated"],
        archived_daily_rows=report["retention"]["archived_daily_rows"],
        partial_days_excluded=partial,
    )


def install_analytics(app, sessions, authorize, scope):
    repository = PostgresEventRepository()

    @app.post("/v1/projects/{project_id}/analytics-dashboards", status_code=201)
    def save_analytics(
        project_id: UUID, body: AnalyticsInput, *, auth: Annotated[dict, Depends(authorize)]
    ) -> AnalyticsDetail:
        scope(auth, project_id, ("editor", "admin"))
        request_hash = fingerprint(body.model_dump(mode="json"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = session.scalar(
                select(SavedView)
                .where(SavedView.project_id == project_id, SavedView.key == body.key)
                .with_for_update()
            )
            if row and row.specification.get("page") != "analytics_dashboard":
                raise HTTPException(409, "SAVED_VIEW_TYPE_CONFLICT")
            if row and row.id != body.dashboard_id:
                raise HTTPException(409, "ANALYTICS_IDENTITY_CONFLICT")
            if (row.revision if row else 0) != body.expected_revision:
                if row and definition(row).request_sha256 == request_hash:
                    return detail(row)
                raise HTTPException(409, "ANALYTICS_REVISION_CONFLICT")
            env = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            if env is None:
                raise HTTPException(422, "ANALYTICS_ENVIRONMENT_NOT_CONFIGURED")
            if row is None:
                if session.get(SavedView, body.dashboard_id):
                    raise HTTPException(409, "ANALYTICS_IDENTITY_CONFLICT")
                if session.scalar(
                    select(AuditEvent.id)
                    .where(
                        AuditEvent.project_id == project_id,
                        AuditEvent.action == "analytics_dashboard_deleted",
                        AuditEvent.details["dashboard_id"].as_string() == str(body.dashboard_id),
                    )
                    .limit(1)
                ):
                    raise HTTPException(409, "ANALYTICS_DASHBOARD_WAS_DELETED")
                count = session.scalar(
                    select(func.count())
                    .select_from(SavedView)
                    .where(SavedView.project_id == project_id)
                )
                if count >= 100:
                    raise HTTPException(429, "SAVED_VIEW_QUOTA_EXCEEDED")
                row = SavedView(
                    id=body.dashboard_id,
                    project_id=project_id,
                    environment_id=env.id,
                    key=body.key,
                    revision=0,
                    specification={},
                )
                session.add(row)
            frozen = AnalyticsDefinition(
                request_sha256=request_hash,
                environment=body.environment,
                window_days=body.window_days,
                panels=body.panels,
            )
            row.specification, row.environment_id = frozen.model_dump(mode="json"), env.id
            row.revision += 1
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="analytics_dashboard_updated",
                    details={
                        "dashboard_id": str(row.id),
                        "revision": row.revision,
                        "panel_count": len(body.panels),
                    },
                )
            )
            session.flush()
            return detail(row)

    @app.get("/v1/projects/{project_id}/analytics-dashboards")
    def list_analytics(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=100),
        *,
        auth: Annotated[dict, Depends(authorize)],
    ) -> AnalyticsPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(SavedView).where(
                SavedView.project_id == project_id,
                SavedView.specification["page"].as_string() == "analytics_dashboard",
            )
            if after:
                query = query.where(SavedView.id > after)
            rows = session.scalars(query.order_by(SavedView.id).limit(limit + 1)).all()
            return AnalyticsPage(
                items=[summary(row) for row in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/analytics-dashboards/{dashboard_id}")
    def get_analytics(
        project_id: UUID, dashboard_id: UUID, *, auth: Annotated[dict, Depends(authorize)]
    ) -> AnalyticsDetail:
        scope(auth, project_id)
        with sessions() as session:
            return detail(scoped(session, project_id, dashboard_id))

    @app.delete("/v1/projects/{project_id}/analytics-dashboards/{dashboard_id}")
    def delete_analytics(
        project_id: UUID,
        dashboard_id: UUID,
        expected_revision: int = Query(ge=1, le=2147483647),
        *,
        auth: Annotated[dict, Depends(authorize)],
    ):
        scope(auth, project_id, ("editor", "admin"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, project_id, dashboard_id, lock=True)
            if row.revision != expected_revision:
                raise HTTPException(409, "ANALYTICS_REVISION_CONFLICT")
            session.delete(row)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="analytics_dashboard_deleted",
                    details={"dashboard_id": str(dashboard_id), "revision": expected_revision},
                )
            )
            return {"deleted": True}

    @app.get("/v1/projects/{project_id}/analytics-dashboards/{dashboard_id}/data")
    def read_analytics(
        project_id: UUID,
        dashboard_id: UUID,
        expected_revision: int = Query(ge=1, le=2147483647),
        *,
        auth: Annotated[dict, Depends(authorize)],
    ) -> AnalyticsData:
        scope(auth, project_id)
        deadline = time.monotonic() + 15
        try:
            with sessions() as session:
                session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                session.execute(text("SET LOCAL statement_timeout = 5000"))
                # Compatible read locks allow concurrent charts while the existing
                # project-erasure lock prevents recreating quota records after deletion.
                if (
                    session.scalar(
                        select(Project.id)
                        .where(Project.id == project_id)
                        .with_for_update(read=True, key_share=True)
                    )
                    is None
                ):
                    raise HTTPException(404, "ANALYTICS_DASHBOARD_NOT_FOUND")
                row = scoped(session, project_id, dashboard_id)
                if row.revision != expected_revision:
                    raise HTTPException(409, "ANALYTICS_REVISION_CONFLICT")
                value = definition(row)
                enforce_quota(sessions, quota_bucket(project_id), 60)
                end = datetime.now(UTC)
                start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                    days=value.window_days - 1
                )
                reports, panels = {}, []
                for panel in value.panels:
                    key = (panel.group_by, panel.traffic_kind, panel.workflow)
                    if key not in reports:
                        reports[key] = repository.query_usage(
                            session,
                            project_id,
                            start,
                            end,
                            panel.group_by,
                            traffic_kind=panel.traffic_kind,
                            environment_name=value.environment,
                            workflow=panel.workflow,
                            group_limit=365 if panel.group_by == "day" else 100,
                            deadline=deadline,
                        )
                    panels.append(panel_data(panel, reports[key], start, end))
                return AnalyticsData(
                    dashboard_id=row.id,
                    revision=row.revision,
                    definition_sha256=summary(row).definition_sha256,
                    environment=value.environment,
                    generated_at=end,
                    starts_at=start,
                    ends_at=end,
                    panels=panels,
                )
        except ValidationError:
            raise HTTPException(409, "ANALYTICS_RETAINED_EVIDENCE_UNAVAILABLE") from None
        except ValueError:
            raise HTTPException(422, "ANALYTICS_QUERY_BOUND_REACHED_NARROW_WINDOW") from None
        except SQLAlchemyError:
            raise HTTPException(503, "ANALYTICS_QUERY_UNAVAILABLE") from None
