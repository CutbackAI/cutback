"""Bounded shared metadata compositions using the existing scoped saved-view store."""

from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select

from cutback.core.request_profiles import fingerprint
from cutback.server.dashboard_contracts import (
    DashboardDefinition,
    DashboardDetail,
    DashboardEvidence,
    DashboardEvidencePage,
    DashboardInput,
    DashboardPage,
    DashboardPanel,
    DashboardPanelDetail,
    DashboardSummary,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent
from cutback.server.monitoring_contracts import SavedViewInput
from cutback.server.monitoring_db import AlertRule, Incident, OnlineRule, SavedView
from cutback.server.prompt_service import environment
from cutback.server.trace_db import ExecutionSpan


def definition(row):
    try:
        return DashboardDefinition.model_validate(row.specification)
    except ValidationError:
        raise HTTPException(409, "DASHBOARD_DEFINITION_UNAVAILABLE") from None


def summary(row):
    value = definition(row)
    return DashboardSummary(
        id=row.id,
        key=row.key,
        revision=row.revision,
        environment=value.environment,
        panel_count=len(value.panels),
        definition_sha256=fingerprint(value.model_dump(mode="json")),
    )


def scoped_dashboard(session, project_id, dashboard_id, *, lock=False):
    row = session.get(SavedView, dashboard_id, with_for_update=lock)
    if not row or row.project_id != project_id or row.specification.get("page") != "dashboard":
        raise HTTPException(404, "DASHBOARD_NOT_FOUND")
    return row


def detail(session, row):
    value = definition(row)
    panels = []
    for panel in value.panels:
        source = session.get(SavedView, panel.view_id)
        state: Literal["current", "changed", "deleted"] = "deleted"
        if source is not None and source.project_id == row.project_id:
            state = "changed"
            try:
                saved = SavedViewInput.model_validate(source.specification)
                current = DashboardPanel(
                    view_id=source.id,
                    revision=source.revision,
                    key=source.key,
                    page=saved.page,
                    environment=saved.environment,
                    filter=saved.filter,
                    incident_state=saved.incident_state,
                )
                if current == panel and source.environment_id == row.environment_id:
                    state = "current"
            except ValidationError:
                pass
        panels.append(DashboardPanelDetail(**panel.model_dump(), source_state=state))
    return DashboardDetail(**summary(row).model_dump(), panels=panels)


def install_dashboards(app, sessions, authorize, scope):
    @app.post("/v1/projects/{project_id}/dashboards", status_code=201)
    def save_dashboard(
        project_id: UUID, body: DashboardInput, auth=Depends(authorize)
    ) -> DashboardDetail:
        scope(auth, project_id, ("editor", "admin"))
        request_hash = fingerprint(body.model_dump(mode="json"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = session.scalar(
                select(SavedView)
                .where(SavedView.project_id == project_id, SavedView.key == body.key)
                .with_for_update()
            )
            if row and row.specification.get("page") != "dashboard":
                raise HTTPException(409, "SAVED_VIEW_TYPE_CONFLICT")
            if (row.revision if row else 0) != body.expected_revision:
                if (
                    row
                    and row.id == body.dashboard_id
                    and definition(row).request_sha256 == request_hash
                ):
                    return detail(session, row)
                raise HTTPException(409, "DASHBOARD_REVISION_CONFLICT")
            env = environment(session, project_id, body.environment)
            if row and row.id != body.dashboard_id:
                raise HTTPException(409, "DASHBOARD_IDENTITY_CONFLICT")
            if not row:
                if session.get(SavedView, body.dashboard_id):
                    raise HTTPException(409, "DASHBOARD_IDENTITY_CONFLICT")
                count = session.scalar(
                    select(func.count())
                    .select_from(SavedView)
                    .where(SavedView.project_id == project_id)
                )
                if count >= 100:
                    raise HTTPException(429, "SAVED_VIEW_QUOTA_EXCEEDED")
            panels = []
            for reference in body.panels:
                source = session.get(SavedView, reference.view_id)
                if not source or source.project_id != project_id:
                    raise HTTPException(404, "DASHBOARD_SOURCE_VIEW_NOT_FOUND")
                if source.environment_id != env.id:
                    raise HTTPException(422, "DASHBOARD_SINGLE_ENVIRONMENT_REQUIRED")
                if source.revision != reference.revision:
                    raise HTTPException(409, "DASHBOARD_SOURCE_VIEW_CHANGED")
                try:
                    saved = SavedViewInput.model_validate(source.specification)
                except ValidationError:
                    raise HTTPException(422, "DASHBOARD_TYPED_LEAF_VIEW_REQUIRED") from None
                panels.append(
                    DashboardPanel(
                        view_id=source.id,
                        revision=source.revision,
                        key=source.key,
                        page=saved.page,
                        environment=saved.environment,
                        filter=saved.filter,
                        incident_state=saved.incident_state,
                    )
                )
            frozen = DashboardDefinition(
                environment=body.environment, request_sha256=request_hash, panels=panels
            )
            if not row:
                row = SavedView(
                    id=body.dashboard_id,
                    project_id=project_id,
                    key=body.key,
                    environment_id=env.id,
                    revision=0,
                    specification={},
                )
                session.add(row)
            row.specification = frozen.model_dump(mode="json")
            row.environment_id, row.revision = env.id, row.revision + 1
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="shared_dashboard_updated",
                    details={
                        "dashboard_id": str(row.id),
                        "revision": row.revision,
                        "panel_count": len(panels),
                    },
                )
            )
            session.flush()
            return detail(session, row)

    @app.get("/v1/projects/{project_id}/dashboards")
    def list_dashboards(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=100),
        auth=Depends(authorize),
    ) -> DashboardPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(SavedView).where(
                SavedView.project_id == project_id,
                SavedView.specification["page"].as_string() == "dashboard",
            )
            if after:
                query = query.where(SavedView.id > after)
            rows = session.scalars(query.order_by(SavedView.id).limit(limit + 1)).all()
            return DashboardPage(
                items=[summary(row) for row in rows[:limit]],
                next_cursor=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/dashboards/{dashboard_id}")
    def get_dashboard(
        project_id: UUID, dashboard_id: UUID, auth=Depends(authorize)
    ) -> DashboardDetail:
        scope(auth, project_id)
        with sessions() as session:
            return detail(session, scoped_dashboard(session, project_id, dashboard_id))

    @app.delete("/v1/projects/{project_id}/dashboards/{dashboard_id}")
    def delete_dashboard(
        project_id: UUID,
        dashboard_id: UUID,
        expected_revision: int = Query(ge=1, le=2147483647),
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("editor", "admin"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped_dashboard(session, project_id, dashboard_id, lock=True)
            if row.revision != expected_revision:
                raise HTTPException(409, "DASHBOARD_REVISION_CONFLICT")
            session.delete(row)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="shared_dashboard_deleted",
                    details={"dashboard_id": str(dashboard_id), "revision": expected_revision},
                )
            )
        return {"status": "deleted"}

    @app.get("/v1/projects/{project_id}/dashboards/{dashboard_id}/panels/{view_id}")
    def panel_evidence(
        project_id: UUID,
        dashboard_id: UUID,
        view_id: UUID,
        expected_revision: int = Query(ge=1, le=2147483647),
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=Depends(authorize),
    ) -> DashboardEvidencePage:
        scope(auth, project_id)
        with sessions() as session:
            dashboard = scoped_dashboard(session, project_id, dashboard_id)
            if dashboard.revision != expected_revision:
                raise HTTPException(409, "DASHBOARD_REVISION_CONFLICT")
            panel = next((p for p in definition(dashboard).panels if p.view_id == view_id), None)
            if panel is None:
                raise HTTPException(404, "DASHBOARD_PANEL_NOT_FOUND")
            selected = panel.filter.model_dump(exclude_none=True)
            basis: Literal["matching_retained_spans", "matching_incident_rule_filters"]
            cursor = None
            if panel.page == "traces":
                query = select(ExecutionSpan).where(
                    ExecutionSpan.project_id == project_id,
                    ExecutionSpan.environment_id == dashboard.environment_id,
                )
                for key, value in selected.items():
                    query = query.where(ExecutionSpan.metadata_record[key].as_string() == value)
                if after:
                    query = query.where(ExecutionSpan.id > after)
                rows = session.scalars(query.order_by(ExecutionSpan.id).limit(limit + 1)).all()
                cursor = rows[limit - 1].id if len(rows) > limit else None
                items = [
                    DashboardEvidence(
                        id=row.id,
                        kind="span",
                        trace_record_id=row.trace_record_id,
                        span_id=row.span_id,
                        workflow=row.metadata_record.get("workflow"),
                        service=row.metadata_record.get("service"),
                        release=row.metadata_record.get("release"),
                        span_kind=row.metadata_record.get("kind"),
                        status=row.metadata_record.get("status"),
                        started_ns=str(row.started_ns),
                    )
                    for row in rows[:limit]
                ]
                basis = "matching_retained_spans"
            else:
                incident_query = (
                    select(Incident)
                    .join(AlertRule, Incident.alert_rule_id == AlertRule.id)
                    .join(OnlineRule, AlertRule.online_rule_id == OnlineRule.id)
                    .where(
                        Incident.project_id == project_id,
                        AlertRule.project_id == project_id,
                        OnlineRule.project_id == project_id,
                        OnlineRule.environment_id == dashboard.environment_id,
                    )
                )
                for key, value in selected.items():
                    incident_query = incident_query.where(
                        OnlineRule.specification["filter"][key].as_string() == value
                    )
                if panel.incident_state:
                    incident_query = incident_query.where(Incident.state == panel.incident_state)
                if after:
                    incident_query = incident_query.where(Incident.id > after)
                incidents = session.scalars(
                    incident_query.order_by(Incident.id).limit(limit + 1)
                ).all()
                cursor = incidents[limit - 1].id if len(incidents) > limit else None
                items = [
                    DashboardEvidence(
                        id=row.id, kind="incident", incident_state=row.state, owner=row.owner
                    )
                    for row in incidents[:limit]
                ]
                basis = "matching_incident_rule_filters"
            return DashboardEvidencePage(
                items=items,
                next_cursor=cursor,
                selection_basis=basis,
            )
