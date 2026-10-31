"""Metadata acknowledgements for privately pre-generated scoped credentials."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cutback.core.bootstrap import ScopedKeyInput, ScopedKeyReceipt, scoped_key_fingerprint
from cutback.server.dataset_lineage import lock_lineage, lock_preparation_identity
from cutback.server.db import AccessKey, AuditEvent, Environment, Project, token_hash

MAX_PROJECT_KEYS = 5000


def enforce_key_quota(session, project):
    """Legacy and resumable issuance share one project lock and retained-key quota."""
    lock_lineage(session, project)
    if (
        session.scalar(
            select(func.count()).select_from(AccessKey).where(AccessKey.project_id == project)
        )
        >= MAX_PROJECT_KEYS
    ):
        raise HTTPException(409, "PROJECT_SCOPED_KEY_QUOTA_EXHAUSTED")


def install_bootstrap_keys(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/scoped-keys", status_code=201, response_model=ScopedKeyReceipt
    )
    def prepare_key(
        project_id: UUID, body: ScopedKeyInput, auth: Annotated[dict, Depends(authorize)]
    ):
        scope(auth, project_id, ("admin",))
        digest = scoped_key_fingerprint(body, project_id=project_id)
        with sessions.begin() as session:
            lock_preparation_identity(session, "scoped-key", body.request_id)
            token_digest = token_hash(body.token.get_secret_value())
            lock_preparation_identity(session, "scoped-key-token", UUID(token_digest[:32]))
            lock_lineage(session, project_id)
            if not session.get(Project, project_id):
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            if not environment:
                raise HTTPException(422, "ENVIRONMENT_NOT_CONFIGURED")
            expected_environment = (
                environment.id if body.role in {"ingest", "prompt_reader"} else None
            )
            receipt_record = session.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.project_id == project_id,
                    AuditEvent.action == "scoped_key_prepared",
                    AuditEvent.details["key_id"].as_string() == str(body.request_id),
                )
                .limit(1)
            )
            row = session.get(AccessKey, body.request_id)
            if row:
                if row.project_id != project_id:
                    raise HTTPException(404, "SCOPED_KEY_NOT_FOUND")
                if (
                    not receipt_record
                    or receipt_record.details.get("request_sha256") != digest
                    or row.role != body.role
                    or row.environment_id != expected_environment
                    or row.expires_at != body.expires_at
                    or row.digest != token_hash(body.token.get_secret_value())
                ):
                    raise HTTPException(409, "SCOPED_KEY_PREPARATION_IDENTITY_CONFLICT")
                if row.revoked_at or row.expires_at is None or row.expires_at <= datetime.now(UTC):
                    raise HTTPException(403, "SCOPED_KEY_EXPIRED_OR_REVOKED")
            else:
                if receipt_record:
                    raise HTTPException(409, "SCOPED_KEY_NO_LONGER_AVAILABLE")
                now = datetime.now(UTC)
                if not now < body.expires_at <= now + timedelta(days=7):
                    raise HTTPException(422, "SCOPED_KEY_EXPIRY_REQUIRES_AT_MOST_SEVEN_DAYS")
                if session.scalar(
                    select(AccessKey.id).where(AccessKey.digest == token_digest).limit(1)
                ):
                    raise HTTPException(409, "SCOPED_KEY_CREDENTIAL_IDENTITY_CONFLICT")
                enforce_key_quota(session, project_id)
                row = AccessKey(
                    id=body.request_id,
                    project_id=project_id,
                    environment_id=expected_environment,
                    digest=token_digest,
                    role=body.role,
                    expires_at=body.expires_at,
                )
                session.add(row)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="scoped_key_prepared",
                        details={
                            "key_id": str(row.id),
                            "role": body.role,
                            "request_sha256": digest,
                        },
                    )
                )
                try:
                    session.flush()
                except IntegrityError:
                    raise HTTPException(409, "SCOPED_KEY_CREDENTIAL_IDENTITY_CONFLICT") from None
        return ScopedKeyReceipt(
            id=row.id,
            project_id=project_id,
            role=body.role,
            environment=body.environment,
            expires_at=row.expires_at,
            request_sha256=digest,
        )
