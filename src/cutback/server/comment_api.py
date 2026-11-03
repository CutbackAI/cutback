"""Explicit encrypted incident comments; listings and audit records stay metadata-only."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from pydantic import Field, field_validator
from sqlalchemy import delete, func, select, tuple_

from cutback.core.models import StrictModel
from cutback.evals.artifacts import configured_store
from cutback.evals.datasets import CapturePolicy, digest
from cutback.server.comment_db import IncidentComment, IncidentCommentVersion
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, CapturePolicyRecord, Environment, PayloadSample
from cutback.server.enterprise_db import ProjectSettings
from cutback.server.evaluation_api import (
    ContentPurpose,
    active_policy,
    policy_object_count,
    require_store,
)
from cutback.server.monitoring_db import AlertRule, Incident, OnlineRule

WORKFLOW = "incident-comments"


class CommentWrite(StrictModel):
    version_id: UUID
    expected_revision: int = Field(ge=0, le=49, strict=True)
    policy_id: UUID
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("COMMENT_TEXT_REQUIRED")
        return value


class CommentDelete(StrictModel):
    expected_revision: int = Field(ge=1, le=50, strict=True)


class CommentContent(StrictModel):
    comment_id: UUID
    revision: int
    text: str
    expires_at: datetime
    evidence: str = "human_comment_not_application_truth"


def parent(session, project, incident_id, *, lock=False):
    row = session.get(Incident, incident_id, with_for_update=lock)
    if not row or row.project_id != project:
        raise HTTPException(404, "INCIDENT_NOT_FOUND")
    return row


def comment(session, project, incident_id, comment_id, *, lock=False):
    row = session.get(IncidentComment, comment_id, with_for_update=lock)
    if not row or row.project_id != project or row.incident_id != incident_id:
        raise HTTPException(404, "INCIDENT_COMMENT_NOT_FOUND")
    return row


def summary(row, auth=None):
    effective = (
        auth.get("project_roles", {}).get(row.project_id)
        if auth and auth["role"] == "oidc"
        else auth["role"]
        if auth
        else None
    )
    editor = effective in {"editor", "project_admin", "admin"}
    return {
        "id": row.id,
        "incident_id": row.incident_id,
        "creator": row.creator,
        "revision": row.revision,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted": row.deleted,
        "deleted_by": row.deleted_by,
        "content": "deleted" if row.deleted else "separate_purpose_scoped_read",
        "can_edit": bool(auth and editor and row.creator == auth["actor"] and not row.deleted),
        "can_delete": bool(
            auth
            and editor
            and not row.deleted
            and (row.creator == auth["actor"] or effective in {"project_admin", "admin"})
        ),
    }


def approved_policy(session, project, incident, policy_id):
    _, policy = active_policy(session, project, policy_id)
    alert = session.get(AlertRule, incident.alert_rule_id)
    rule = session.get(OnlineRule, alert.online_rule_id) if alert else None
    env = session.get(Environment, rule.environment_id) if rule else None
    if (
        not env
        or env.project_id != project
        or not policy.permits(env.name, WORKFLOW)
        or "local" not in policy.allowed_providers
        or policy.sample_rate != 1
    ):
        raise HTTPException(403, "INCIDENT_COMMENT_POLICY_SCOPE_REQUIRED")
    return policy, env.name


def audit(session, project, actor, action, row, **details):
    session.add(
        AuditEvent(
            project_id=project,
            actor=actor,
            action=action,
            details={
                "incident_id": str(row.incident_id),
                "comment_id": str(row.id),
                "revision": row.revision,
                **details,
            },
        )
    )


def install_comments(app, sessions, authorize, scope):
    base = "/v1/projects/{project_id}/incidents/{incident_id}/comments"

    @app.get(base)
    def list_comments(
        project_id: UUID,
        incident_id: UUID,
        after: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            parent(session, project_id, incident_id)
            query = select(IncidentComment).where(
                IncidentComment.project_id == project_id, IncidentComment.incident_id == incident_id
            )
            if after:
                cursor = comment(session, project_id, incident_id, after)
                query = query.where(
                    tuple_(IncidentComment.created_at, IncidentComment.id)
                    > (cursor.created_at, cursor.id)
                )
            rows = session.scalars(
                query.order_by(IncidentComment.created_at, IncidentComment.id).limit(limit + 1)
            ).all()
            return {
                "items": [summary(row, auth) for row in rows[:limit]],
                "next_cursor": rows[limit - 1].id if len(rows) > limit else None,
                "content_included": False,
            }

    @app.get(base + "/policies")
    def comment_policies(project_id: UUID, incident_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions() as session:
            incident = parent(session, project_id, incident_id)
            alert = session.get(AlertRule, incident.alert_rule_id)
            rule = session.get(OnlineRule, alert.online_rule_id) if alert else None
            env = session.get(Environment, rule.environment_id) if rule else None
            if not env or env.project_id != project_id:
                raise HTTPException(404, "INCIDENT_NOT_FOUND")
            rows = session.scalars(
                select(CapturePolicyRecord)
                .where(
                    CapturePolicyRecord.project_id == project_id,
                    CapturePolicyRecord.enabled.is_(True),
                )
                .order_by(CapturePolicyRecord.id)
                .limit(101)
            ).all()
            items = []
            for row in rows[:100]:
                policy = CapturePolicy.model_validate(row.policy)
                if (
                    policy.permits(env.name, WORKFLOW)
                    and "local" in policy.allowed_providers
                    and policy.sample_rate == 1
                ):
                    items.append(
                        {
                            "id": row.id,
                            "approval_ref": policy.approval_ref,
                            "expires_at": policy.expires_at,
                            "retention_days": policy.retention_days,
                        }
                    )
            return {
                "items": items,
                "environment": env.name,
                "policies_examined": min(len(rows), 100),
                "truncated": len(rows) > 100,
            }

    @app.put(base + "/{comment_id}", status_code=201)
    def write_comment(
        project_id: UUID,
        incident_id: UUID,
        comment_id: UUID,
        body: CommentWrite,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("editor",))
        store = require_store(app)
        ref = None
        try:
            with sessions.begin() as session:
                lock_lineage(session, project_id)
                incident = parent(session, project_id, incident_id, lock=True)
                policy, environment = approved_policy(session, project_id, incident, body.policy_id)
                try:
                    clean, blob = policy_content({"text": body.text}, policy)
                except ValueError:
                    raise HTTPException(422, "INCIDENT_COMMENT_CONTENT_POLICY_REJECTED") from None
                if not isinstance(clean.get("text"), str) or not clean["text"].strip():
                    raise HTTPException(422, "INCIDENT_COMMENT_CONTENT_POLICY_REJECTED")
                row = session.get(IncidentComment, comment_id, with_for_update=True)
                if row and (row.project_id != project_id or row.incident_id != incident_id):
                    raise HTTPException(409, "INCIDENT_COMMENT_IDENTITY_CONFLICT")
                if row and row.creator != auth["actor"]:
                    raise HTTPException(403, "INCIDENT_COMMENT_AUTHOR_REQUIRED")
                if row and row.deleted:
                    raise HTTPException(410, "INCIDENT_COMMENT_DELETED")
                existing = session.get(IncidentCommentVersion, body.version_id)
                if existing:
                    payload = session.get(PayloadSample, existing.payload_id)
                    if (
                        row
                        and existing.comment_id == row.id
                        and existing.project_id == project_id
                        and existing.actor == auth["actor"]
                        and existing.revision == body.expected_revision + 1
                        and payload
                        and payload.policy_id == body.policy_id
                        and payload.expires_at > datetime.now(UTC)
                        and payload.content_hash == digest(clean)
                    ):
                        return {
                            "comment": summary(row, auth),
                            "accepted_revision": existing.revision,
                            "duplicate": True,
                        }
                    raise HTTPException(409, "INCIDENT_COMMENT_VERSION_IDENTITY_CONFLICT")
                if (row.revision if row else 0) != body.expected_revision:
                    raise HTTPException(409, "INCIDENT_COMMENT_REVISION_CONFLICT")
                if session.get(PayloadSample, body.version_id):
                    raise HTTPException(409, "INCIDENT_COMMENT_VERSION_IDENTITY_CONFLICT")
                if not row:
                    for predicates, maximum in (
                        ([IncidentComment.project_id == project_id], 10000),
                        (
                            [
                                IncidentComment.project_id == project_id,
                                IncidentComment.incident_id == incident_id,
                            ],
                            200,
                        ),
                    ):
                        if (
                            session.scalar(
                                select(func.count()).select_from(IncidentComment).where(*predicates)
                            )
                            >= maximum
                        ):
                            raise HTTPException(429, "INCIDENT_COMMENT_QUOTA_EXCEEDED")
                if policy_object_count(session, body.policy_id) >= policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                now = datetime.now(UTC)
                if not row:
                    row = IncidentComment(
                        id=comment_id,
                        project_id=project_id,
                        incident_id=incident_id,
                        creator=auth["actor"],
                        revision=body.expected_revision + 1,
                        current_version_id=body.version_id,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    session.flush()
                ref = store.put(project_id, blob, policy.retention_days)
                settings = session.get(ProjectSettings, project_id)
                expires = min(
                    policy.expires_at,
                    now + timedelta(days=policy.retention_days),
                    now + timedelta(days=settings.raw_days if settings else 30),
                )
                row.revision = body.expected_revision + 1
                row.current_version_id, row.updated_at = body.version_id, now
                session.add(
                    PayloadSample(
                        id=body.version_id,
                        project_id=project_id,
                        policy_id=body.policy_id,
                        object_ref=ref,
                        content_hash=digest(clean),
                        expires_at=expires,
                        metadata_safe={
                            "kind": "incident_comment",
                            "environment": environment,
                            "workflow": WORKFLOW,
                            "provider": "local",
                            "comment_id": str(row.id),
                            "revision": row.revision,
                        },
                    )
                )
                session.flush()
                session.add(
                    IncidentCommentVersion(
                        id=body.version_id,
                        project_id=project_id,
                        comment_id=row.id,
                        payload_id=body.version_id,
                        revision=row.revision,
                        actor=auth["actor"],
                        created_at=now,
                    )
                )
                audit(
                    session,
                    project_id,
                    auth["actor"],
                    "incident_comment_written",
                    row,
                    policy_id=str(body.policy_id),
                    version_id=str(body.version_id),
                )
                result = {
                    "comment": summary(row, auth),
                    "accepted_revision": row.revision,
                    "duplicate": False,
                }
            return result
        except BaseException:
            if ref:
                # A lost commit acknowledgment is not proof of rollback. Never
                # erase a blob still referenced by committed canonical state.
                # If the database is unavailable, retain the encrypted object
                # under its existing expiry rather than guess its ownership.
                try:
                    with sessions() as check:
                        retained = check.get(PayloadSample, body.version_id)
                        referenced = retained and retained.object_ref == ref
                    if not referenced:
                        store.delete(project_id, ref)
                except Exception:
                    pass
            raise

    @app.get(base + "/{comment_id}/versions")
    def versions(project_id: UUID, incident_id: UUID, comment_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = comment(session, project_id, incident_id, comment_id)
            rows = session.scalars(
                select(IncidentCommentVersion)
                .where(
                    IncidentCommentVersion.comment_id == row.id,
                    IncidentCommentVersion.project_id == project_id,
                )
                .order_by(IncidentCommentVersion.revision.desc())
                .limit(50)
            ).all()
            return {
                "items": [
                    {
                        "revision": version.revision,
                        "actor": version.actor,
                        "created_at": version.created_at,
                    }
                    for version in rows
                ],
                "content_included": False,
                "history": "retained_revisions_only",
            }

    @app.post(base + "/{comment_id}/content", response_model=CommentContent)
    def read_content(
        project_id: UUID,
        incident_id: UUID,
        comment_id: UUID,
        body: ContentPurpose,
        revision: int | None = Query(None, ge=1, le=50),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            incident = parent(session, project_id, incident_id, lock=True)
            row = comment(session, project_id, incident_id, comment_id, lock=True)
            if row.deleted:
                raise HTTPException(410, "INCIDENT_COMMENT_DELETED")
            version = session.scalar(
                select(IncidentCommentVersion).where(
                    IncidentCommentVersion.project_id == project_id,
                    IncidentCommentVersion.comment_id == row.id,
                    IncidentCommentVersion.revision == (revision or row.revision),
                )
            )
            payload = session.get(PayloadSample, version.payload_id) if version else None
            if not payload or payload.expires_at <= datetime.now(UTC):
                raise HTTPException(410, "INCIDENT_COMMENT_CONTENT_UNAVAILABLE")
            policy, _ = approved_policy(session, project_id, incident, payload.policy_id)
            try:
                blob = store.get(project_id, payload.object_ref)
                if len(blob) > policy.max_payload_bytes:
                    raise ValueError("INCIDENT_COMMENT_SIZE_CHANGED")
                value = json.loads(blob)
                if digest(value) != payload.content_hash:
                    raise ValueError("INCIDENT_COMMENT_INTEGRITY_FAILED")
                clean, _ = policy_content(value, policy)
                if not isinstance(clean.get("text"), str) or not clean["text"].strip():
                    raise ValueError("INCIDENT_COMMENT_TEXT_UNAVAILABLE")
            except (OSError, ValueError, InvalidTag):
                raise HTTPException(410, "INCIDENT_COMMENT_CONTENT_UNAVAILABLE") from None
            audit(
                session,
                project_id,
                auth["actor"],
                "incident_comment_read",
                row,
                read_revision=version.revision,
                purpose=body.purpose,
            )
            return CommentContent(
                comment_id=row.id,
                revision=version.revision,
                text=clean["text"],
                expires_at=payload.expires_at,
            )

    @app.post(base + "/{comment_id}/delete")
    def delete_comment(
        project_id: UUID,
        incident_id: UUID,
        comment_id: UUID,
        body: CommentDelete,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            parent(session, project_id, incident_id, lock=True)
            row = comment(session, project_id, incident_id, comment_id, lock=True)
            if row.creator != auth["actor"]:
                scope(auth, project_id, ("admin",))
            if (
                row.deleted
                and row.revision == body.expected_revision + 1
                and row.deleted_by == auth["actor"]
            ):
                return {"comment": summary(row, auth), "duplicate": True}
            if row.deleted or row.revision != body.expected_revision:
                raise HTTPException(409, "INCIDENT_COMMENT_REVISION_CONFLICT")
            rows = session.scalars(
                select(PayloadSample)
                .join(IncidentCommentVersion, IncidentCommentVersion.payload_id == PayloadSample.id)
                .where(
                    IncidentCommentVersion.comment_id == row.id,
                    IncidentCommentVersion.project_id == project_id,
                )
                .with_for_update()
            ).all()
            if rows:
                # Revoked capture or a storage hard deny must not prevent erasure.
                try:
                    store = getattr(app.state, "artifact_store", None) or configured_store()
                    for payload in rows:
                        store.delete(project_id, payload.object_ref)
                except (OSError, ValueError):
                    raise HTTPException(503, "INCIDENT_COMMENT_ERASURE_UNCONFIRMED") from None
                session.execute(
                    delete(PayloadSample).where(
                        PayloadSample.project_id == project_id,
                        PayloadSample.id.in_([item.id for item in rows]),
                    )
                )
            row.deleted, row.deleted_by = True, auth["actor"]
            row.current_version_id = None
            row.revision += 1
            row.updated_at = datetime.now(UTC)
            audit(
                session,
                project_id,
                auth["actor"],
                "incident_comment_deleted",
                row,
                erased_payloads=len(rows),
            )
            return {"comment": summary(row, auth), "duplicate": False}
