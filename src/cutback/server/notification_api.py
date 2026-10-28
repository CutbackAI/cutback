"""Explicit opt-in to operator-allowlisted metadata-only incident notifications."""

from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import Field, StrictBool, model_validator
from sqlalchemy import func, select, update

from cutback.core.models import Identifier, StrictModel
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent
from cutback.server.monitoring_db import NotificationConfig, NotificationDelivery
from cutback.server.notifications import destination_hash, destinations
from cutback.server.prompt_contracts import PromptKey


class NotificationInput(StrictModel):
    destination: PromptKey
    expected_revision: int = Field(ge=0, le=2147483646)
    enabled: StrictBool = False
    authorize_delivery: StrictBool = False
    approval_ref: Identifier

    @model_validator(mode="after")
    def authorized(self):
        if self.enabled and not self.authorize_delivery:
            raise ValueError("EXPLICIT_EXTERNAL_DELIVERY_AUTHORIZATION_REQUIRED")
        return self


def install_notifications(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/notification-destinations")
    def allowed(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        try:
            identifiers = [
                key
                for key, entry in destinations().items()
                if str(project_id) in entry["project_ids"]
            ]
        except ValueError:
            raise HTTPException(503, "WEBHOOK_OPERATOR_CONFIGURATION_INVALID") from None
        return {
            "identifiers": identifiers,
            "content": "incident_metadata_only",
            "configuration_authorizes_future_delivery": True,
        }

    @app.post("/v1/projects/{project_id}/notifications", status_code=201)
    def configure(project_id: UUID, body: NotificationInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        try:
            entry = destinations().get(body.destination)
        except ValueError:
            raise HTTPException(503, "WEBHOOK_OPERATOR_CONFIGURATION_INVALID") from None
        if not entry or str(project_id) not in entry["project_ids"]:
            raise HTTPException(403, "DESTINATION_NOT_OPERATOR_ALLOWLISTED")
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = session.scalar(
                select(NotificationConfig)
                .where(
                    NotificationConfig.project_id == project_id,
                    NotificationConfig.destination == body.destination,
                )
                .with_for_update()
            )
            if (row.revision if row else 0) != body.expected_revision:
                raise HTTPException(409, "NOTIFICATION_REVISION_CONFLICT")
            if not row:
                count = session.scalar(
                    select(func.count())
                    .select_from(NotificationConfig)
                    .where(NotificationConfig.project_id == project_id)
                )
                if count >= 10:
                    raise HTTPException(429, "PROJECT_NOTIFICATION_DESTINATION_QUOTA_EXCEEDED")
                row = NotificationConfig(
                    project_id=project_id,
                    destination=body.destination,
                    destination_hash=destination_hash(entry),
                    revision=0,
                    approval_ref=body.approval_ref,
                )
                session.add(row)
            row.enabled, row.approval_ref, row.destination_hash = (
                body.enabled,
                body.approval_ref,
                destination_hash(entry),
            )
            row.revision += 1
            if not body.enabled:
                session.execute(
                    update(NotificationDelivery)
                    .where(
                        NotificationDelivery.config_id == row.id,
                        NotificationDelivery.state == "queued",
                    )
                    .values(state="cancelled", code="DESTINATION_DISABLED")
                )
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="notification_configuration_approved",
                    details={
                        "config_id": str(row.id),
                        "revision": row.revision,
                        "enabled": row.enabled,
                    },
                )
            )
            return {
                "id": str(row.id),
                "destination": row.destination,
                "revision": row.revision,
                "enabled": row.enabled,
            }

    @app.get("/v1/projects/{project_id}/notifications")
    def configs(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.scalars(
                select(NotificationConfig)
                .where(NotificationConfig.project_id == project_id)
                .order_by(NotificationConfig.id)
                .limit(10)
            ).all()
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "destination": r.destination,
                        "enabled": r.enabled,
                        "revision": r.revision,
                    }
                    for r in rows
                ]
            }

    @app.get("/v1/projects/{project_id}/notification-deliveries")
    def deliveries(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(NotificationDelivery).where(
                NotificationDelivery.project_id == project_id
            )
            if after:
                query = query.where(NotificationDelivery.id > after)
            rows = session.scalars(query.order_by(NotificationDelivery.id).limit(limit + 1)).all()
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "config_id": str(r.config_id),
                        "event_id": str(r.event_id),
                        "state": r.state,
                        "attempts": r.attempts,
                        "code": r.code,
                    }
                    for r in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "delivery_semantics": "at_least_once_deduplicate_delivery_id",
            }
