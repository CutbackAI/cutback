"""Project-scoped continuous-quality control; no inline inference or notifications."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select, update

from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent
from cutback.server.incidents import incident_event
from cutback.server.monitoring_contracts import (
    AlertRuleInput,
    IncidentTransition,
    OnlineRuleInput,
    RuleSwitch,
    SavedViewInput,
)
from cutback.server.monitoring_db import (
    AlertRule,
    AlertWindow,
    Incident,
    IncidentEvent,
    OnlineEvaluation,
    OnlineIntake,
    OnlineRule,
    SavedView,
)
from cutback.server.prompt_service import environment
from cutback.server.trace_db import ExecutionSpan


def metadata(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def scoped(session, cls, project, identifier, *, lock=False):
    row = session.get(cls, identifier, with_for_update=lock)
    if not row or row.project_id != project:
        raise HTTPException(404, "MONITORING_RECORD_NOT_FOUND")
    return row


def evaluation_metadata(session, row):
    result = {k: v for k, v in metadata(row).items() if k != "lease_token"}
    result["trace_record_id"] = (
        session.scalar(
            select(ExecutionSpan.trace_record_id).where(
                ExecutionSpan.id == row.span_record_id, ExecutionSpan.project_id == row.project_id
            )
        )
        if row.span_record_id
        else None
    )
    return result


def new_revision(session, cls, project, body):
    lock_lineage(session, project)
    previous = session.scalar(
        select(cls)
        .where(cls.project_id == project, cls.key == body.key)
        .order_by(cls.revision.desc())
        .limit(1)
    )
    existing = session.get(cls, body.version_id)
    if existing:
        if existing.project_id == project and existing.specification == body.model_dump(
            mode="json"
        ):
            return existing
        raise HTTPException(409, "MONITORING_VERSION_IDENTITY_CONFLICT")
    if (previous.revision if previous else 0) != body.expected_revision:
        raise HTTPException(409, "MONITORING_REVISION_CONFLICT")
    count = session.scalar(select(func.count()).select_from(cls).where(cls.project_id == project))
    if count >= 1000:
        raise HTTPException(429, "PROJECT_MONITORING_RULE_QUOTA_EXCEEDED")
    if previous:
        previous.enabled = False
        if cls is OnlineRule:
            session.execute(
                update(OnlineEvaluation)
                .where(OnlineEvaluation.rule_id == previous.id, OnlineEvaluation.state == "queued")
                .values(state="unknown", result={"code": "RULE_SUPERSEDED"})
            )
            session.execute(
                update(AlertRule)
                .where(AlertRule.online_rule_id == previous.id)
                .values(backfill_complete=False, backfill_cursor=None)
            )
    return None


def install_monitoring(app, sessions, authorize, scope):
    @app.post("/v1/projects/{project_id}/online-rules", status_code=201)
    def create_online(project_id: UUID, body: OnlineRuleInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        now = datetime.now(UTC)
        if (
            body.starts_at < now - timedelta(days=30)
            or body.expires_at <= now
            or body.starts_at > now + timedelta(days=1)
        ):
            raise HTTPException(422, "ONLINE_RULE_WINDOW_OUTSIDE_ALLOWED_RANGE")
        with sessions.begin() as session:
            existing = new_revision(session, OnlineRule, project_id, body)
            if existing:
                return metadata(existing)
            env = environment(session, project_id, body.environment)
            active = session.scalar(
                select(func.count())
                .select_from(OnlineRule)
                .where(
                    OnlineRule.project_id == project_id,
                    OnlineRule.environment_id == env.id,
                    OnlineRule.enabled.is_(True),
                )
            )
            if active >= 50:
                raise HTTPException(429, "ENVIRONMENT_ONLINE_RULE_QUOTA_EXCEEDED")
            row = OnlineRule(
                id=body.version_id,
                project_id=project_id,
                environment_id=env.id,
                key=body.key,
                revision=body.expected_revision + 1,
                specification=body.model_dump(mode="json"),
                creator=auth["actor"],
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="online_rule_created",
                    details={
                        "rule_id": str(row.id),
                        "revision": row.revision,
                        "profile": "nonbillable_metadata_v1",
                    },
                )
            )
            return metadata(row)

    @app.get("/v1/projects/{project_id}/online-rules")
    def list_online(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(OnlineRule).where(OnlineRule.project_id == project_id)
            if after:
                query = query.where(OnlineRule.id > after)
            rows = session.scalars(query.order_by(OnlineRule.id).limit(limit + 1)).all()
            pending = session.scalar(
                select(func.count())
                .select_from(OnlineIntake)
                .where(OnlineIntake.project_id == project_id)
            )
            return {
                "pending_intake": pending,
                "items": [metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "execution_profile": "nonbillable_metadata_v1",
            }

    @app.post("/v1/projects/{project_id}/online-rules/{rule_id}/switch")
    def switch_online(project_id: UUID, rule_id: UUID, body: RuleSwitch, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, OnlineRule, project_id, rule_id, lock=True)
            latest = session.scalar(
                select(func.max(OnlineRule.revision)).where(
                    OnlineRule.project_id == project_id, OnlineRule.key == row.key
                )
            )
            if (
                body.expected_revision != row.revision
                or latest != row.revision
                or row.enabled != body.expected_enabled
            ):
                raise HTTPException(409, "ONLY_CURRENT_RULE_VERSION_CAN_BE_SWITCHED")
            if body.enabled:
                spec = OnlineRuleInput.model_validate(row.specification)
                if datetime.now(UTC) >= spec.expires_at:
                    raise HTTPException(409, "ONLINE_RULE_EXPIRED")
                count = session.scalar(
                    select(func.count())
                    .select_from(OnlineRule)
                    .where(
                        OnlineRule.project_id == project_id,
                        OnlineRule.environment_id == row.environment_id,
                        OnlineRule.enabled.is_(True),
                        OnlineRule.id != row.id,
                    )
                )
                if count >= 50:
                    raise HTTPException(429, "ENVIRONMENT_ONLINE_RULE_QUOTA_EXCEEDED")
            row.enabled = body.enabled
            if not body.enabled:
                session.execute(
                    update(OnlineEvaluation)
                    .where(OnlineEvaluation.rule_id == row.id, OnlineEvaluation.state == "queued")
                    .values(state="unknown", result={"code": "RULE_DISABLED"})
                )
                session.execute(
                    update(AlertRule)
                    .where(AlertRule.online_rule_id == row.id)
                    .values(backfill_complete=False, backfill_cursor=None)
                )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="online_rule_switched",
                    details={"rule_id": str(row.id), "enabled": row.enabled},
                )
            )
            return metadata(row)

    @app.get("/v1/projects/{project_id}/online-rules/{rule_id}/evaluations")
    def evaluations(
        project_id: UUID,
        rule_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped(session, OnlineRule, project_id, rule_id)
            query = select(OnlineEvaluation).where(OnlineEvaluation.rule_id == rule_id)
            if after:
                query = query.where(OnlineEvaluation.id > after)
            rows = session.scalars(query.order_by(OnlineEvaluation.id).limit(limit + 1)).all()
            # Lease capabilities are never exposed to API readers.
            return {
                "items": [evaluation_metadata(session, r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "population_coverage": "unknown",
            }

    @app.post("/v1/projects/{project_id}/alert-rules", status_code=201)
    def create_alert(project_id: UUID, body: AlertRuleInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            existing = new_revision(session, AlertRule, project_id, body)
            if existing:
                return metadata(existing)
            scoped(session, OnlineRule, project_id, body.online_rule_id)
            active = session.scalar(
                select(func.count())
                .select_from(AlertRule)
                .where(AlertRule.online_rule_id == body.online_rule_id, AlertRule.enabled.is_(True))
            )
            if active >= 10:
                raise HTTPException(429, "ONLINE_RULE_ALERT_QUOTA_EXCEEDED")
            row = AlertRule(
                id=body.version_id,
                project_id=project_id,
                online_rule_id=body.online_rule_id,
                key=body.key,
                revision=body.expected_revision + 1,
                specification=body.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="alert_rule_created",
                    details={"rule_id": str(row.id)},
                )
            )
            return metadata(row)

    @app.get("/v1/projects/{project_id}/alert-rules")
    def list_alerts(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(AlertRule).where(AlertRule.project_id == project_id)
            if after:
                query = query.where(AlertRule.id > after)
            rows = session.scalars(query.order_by(AlertRule.id).limit(limit + 1)).all()
            return {
                "items": [metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/alert-rules/{rule_id}/switch")
    def switch_alert(project_id: UUID, rule_id: UUID, body: RuleSwitch, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, AlertRule, project_id, rule_id, lock=True)
            latest = session.scalar(
                select(func.max(AlertRule.revision)).where(
                    AlertRule.project_id == project_id, AlertRule.key == row.key
                )
            )
            if (
                body.expected_revision != row.revision
                or latest != row.revision
                or row.enabled != body.expected_enabled
            ):
                raise HTTPException(409, "ONLY_CURRENT_ALERT_VERSION_CAN_BE_SWITCHED")
            if body.enabled:
                count = session.scalar(
                    select(func.count())
                    .select_from(AlertRule)
                    .where(
                        AlertRule.online_rule_id == row.online_rule_id,
                        AlertRule.enabled.is_(True),
                        AlertRule.id != row.id,
                    )
                )
                if count >= 10:
                    raise HTTPException(429, "ONLINE_RULE_ALERT_QUOTA_EXCEEDED")
            row.enabled = body.enabled
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="alert_rule_switched",
                    details={"rule_id": str(row.id), "enabled": row.enabled},
                )
            )
            return metadata(row)

    @app.get("/v1/projects/{project_id}/alert-rules/{rule_id}/windows")
    def alert_windows(
        project_id: UUID,
        rule_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped(session, AlertRule, project_id, rule_id)
            query = select(AlertWindow).where(AlertWindow.alert_rule_id == rule_id)
            if after:
                query = query.where(AlertWindow.id > after)
            rows = session.scalars(query.order_by(AlertWindow.id).limit(limit + 1)).all()
            return {
                "items": [metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "retention_coverage": "unknown",
            }

    @app.get("/v1/projects/{project_id}/incidents")
    def incidents(
        project_id: UUID,
        after: UUID | None = None,
        state: str | None = Query(None, pattern="^(open|acknowledged|resolved)$"),
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(Incident).where(Incident.project_id == project_id)
            if state:
                query = query.where(Incident.state == state)
            if after:
                query = query.where(Incident.id > after)
            rows = session.scalars(query.order_by(Incident.id).limit(limit + 1)).all()
            return {
                "items": [metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.get("/v1/projects/{project_id}/incidents/{incident_id}")
    def incident_detail(project_id: UUID, incident_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = scoped(session, Incident, project_id, incident_id)
            events = session.scalars(
                select(IncidentEvent)
                .where(IncidentEvent.incident_id == row.id)
                .order_by(IncidentEvent.revision)
                .limit(501)
            ).all()
            return {
                "incident": metadata(row),
                "events": [metadata(e) for e in events[:500]],
                "events_truncated": len(events) > 500,
            }

    @app.post("/v1/projects/{project_id}/incidents/{incident_id}/transition")
    def transition(
        project_id: UUID, incident_id: UUID, body: IncidentTransition, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("editor", "admin"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, Incident, project_id, incident_id, lock=True)
            if row.revision != body.expected_revision or row.state == "resolved":
                raise HTTPException(409, "INCIDENT_TRANSITION_CONFLICT")
            if body.action == "acknowledge":
                if row.state != "open":
                    raise HTTPException(409, "INCIDENT_ALREADY_ACKNOWLEDGED")
                row.state = "acknowledged"
            elif body.action == "resolve":
                row.state = "resolved"
            else:
                # Owner is a team-visible routing reference, not an access grant.
                row.owner = body.owner
            row.revision += 1
            incident_event(session, row, auth["actor"], body.action)
            return metadata(row)

    @app.get("/v1/projects/{project_id}/incidents/{incident_id}/evaluations")
    def incident_evaluations(
        project_id: UUID,
        incident_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            incident = scoped(session, Incident, project_id, incident_id)
            alert = scoped(session, AlertRule, project_id, incident.alert_rule_id)
            query = select(OnlineEvaluation).where(
                OnlineEvaluation.rule_id == alert.online_rule_id,
                OnlineEvaluation.occurred_at >= incident.window_start,
                OnlineEvaluation.occurred_at < incident.window_end,
            )
            if after:
                query = query.where(OnlineEvaluation.id > after)
            rows = session.scalars(query.order_by(OnlineEvaluation.id).limit(limit + 1)).all()
            return {
                "items": [evaluation_metadata(session, r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "retention_coverage": "unknown",
            }

    @app.post("/v1/projects/{project_id}/saved-views", status_code=201)
    def save_view(project_id: UUID, body: SavedViewInput, auth=Depends(authorize)):
        scope(auth, project_id, ("editor", "admin"))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = session.scalar(
                select(SavedView)
                .where(SavedView.project_id == project_id, SavedView.key == body.key)
                .with_for_update()
            )
            if (row.revision if row else 0) != body.expected_revision:
                if row and row.specification == body.model_dump(mode="json"):
                    return metadata(row)
                raise HTTPException(409, "SAVED_VIEW_REVISION_CONFLICT")
            env = environment(session, project_id, body.environment)
            if not row:
                if session.get(SavedView, body.view_id):
                    raise HTTPException(409, "SAVED_VIEW_IDENTITY_CONFLICT")
                count = session.scalar(
                    select(func.count())
                    .select_from(SavedView)
                    .where(SavedView.project_id == project_id)
                )
                if count >= 100:
                    raise HTTPException(429, "SAVED_VIEW_QUOTA_EXCEEDED")
                row = SavedView(
                    id=body.view_id,
                    project_id=project_id,
                    key=body.key,
                    environment_id=env.id,
                    revision=0,
                    specification={},
                )
                session.add(row)
            elif row.id != body.view_id:
                raise HTTPException(409, "SAVED_VIEW_IDENTITY_CONFLICT")
            row.specification, row.environment_id, row.revision = (
                body.model_dump(mode="json"),
                env.id,
                row.revision + 1,
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="saved_view_updated",
                    details={"view_id": str(row.id), "revision": row.revision},
                )
            )
            session.flush()
            return metadata(row)

    @app.get("/v1/projects/{project_id}/saved-views")
    def saved_views(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return {
                "items": [
                    metadata(r)
                    for r in session.scalars(
                        select(SavedView)
                        .where(SavedView.project_id == project_id)
                        .order_by(SavedView.key)
                        .limit(100)
                    )
                ]
            }

    @app.delete("/v1/projects/{project_id}/saved-views/{view_id}")
    def delete_view(
        project_id: UUID,
        view_id: UUID,
        expected_revision: int = Query(ge=1),
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("editor", "admin"))
        with sessions.begin() as session:
            row = scoped(session, SavedView, project_id, view_id, lock=True)
            if row.revision != expected_revision:
                raise HTTPException(409, "SAVED_VIEW_REVISION_CONFLICT")
            session.delete(row)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="saved_view_deleted",
                    details={"view_id": str(view_id)},
                )
            )
        return {"status": "deleted"}
