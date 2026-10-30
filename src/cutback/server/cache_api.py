"""Explicit encrypted, project-scoped immutable response cache; disabled by default."""

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import delete, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import CapturePolicy, digest, redact
from cutback.server.db import AuditEvent, CapturePolicyRecord, Environment
from cutback.server.enterprise_db import CacheEntry


class CacheKey(StrictModel):
    project_id: UUID
    policy_id: UUID
    key_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    workflow: Identifier
    environment: Identifier
    provider: Literal["openai", "anthropic", "local"]
    model: Identifier
    prompt_revision: Identifier
    freshness_ref: Identifier


class CachePut(CacheKey):
    response: str = Field(min_length=1, max_length=64000)
    ttl_seconds: int = Field(ge=1, le=2592000)
    immutable_or_safely_invalidated: Literal[True]


def enabled():
    if os.getenv("CUTBACK_CACHE_ENABLED", "false").lower() != "true":
        raise HTTPException(503, "APPLICATION_CACHE_DISABLED")


def cache_policy(session, body, auth, scope):
    scope(auth, body.project_id, ("ingest", "editor"))
    if auth["role"] == "ingest":
        environment = session.get(Environment, auth["environment_id"])
        if not environment or environment.name != body.environment:
            raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
    row = session.get(CapturePolicyRecord, body.policy_id, with_for_update=True)
    if not row or row.project_id != body.project_id or not row.enabled:
        raise HTTPException(403, "CACHE_POLICY_DENIED")
    policy = CapturePolicy.model_validate(row.policy)
    if (
        not policy.permits(body.environment, body.workflow)
        or body.provider not in policy.allowed_providers
    ):
        raise HTTPException(403, "CACHE_POLICY_DENIED")
    return policy


def key_digest(body):
    return digest(
        CacheKey.model_validate(body.model_dump(include=set(CacheKey.model_fields))).model_dump(
            mode="json"
        )
    )


def install_cache(app, sessions, authorize, scope):
    from cutback.server.evaluation_api import require_store

    @app.post("/v1/ingest/cache/lookup")
    def lookup(body: CacheKey, auth=Depends(authorize)):
        enabled()
        store = require_store(app)
        with sessions.begin() as session:
            cache_policy(session, body, auth, scope)
            row = session.scalar(
                select(CacheEntry).where(
                    CacheEntry.project_id == body.project_id,
                    CacheEntry.key_hash == key_digest(body),
                )
            )
            if not row or row.expires_at <= datetime.now(UTC):
                return {"hit": False, "response": None}
            try:
                value = json.loads(store.get(body.project_id, row.object_ref))
                if value["key_hash"] != row.key_hash:
                    raise ValueError("CACHE_INTEGRITY_FAILURE")
            except (ValueError, OSError, KeyError, InvalidTag):
                return {"hit": False, "response": None, "reason": "expired_or_unavailable"}
            return {
                "hit": True,
                "response": value["response"],
                "expires_at": row.expires_at.isoformat(),
                "cache_id": str(row.id),
            }

    @app.post("/v1/ingest/cache/put", status_code=201)
    def put(body: CachePut, auth=Depends(authorize)):
        enabled()
        store = require_store(app)
        object_ref = None
        committed = False
        try:
            with sessions.begin() as session:
                policy = cache_policy(session, body, auth, scope)
                if body.ttl_seconds > policy.retention_days * 86400:
                    raise HTTPException(422, "CACHE_TTL_EXCEEDS_CAPTURE_POLICY")
                key = key_digest(body)
                existing = session.scalar(
                    select(CacheEntry).where(
                        CacheEntry.project_id == body.project_id, CacheEntry.key_hash == key
                    )
                )
                if existing:
                    raise HTTPException(409, "CACHE_KEY_IMMUTABLE_INVALIDATE_OR_CHANGE_REVISION")
                from cutback.server.evaluation_api import policy_object_count

                if policy_object_count(session, body.policy_id) >= min(policy.max_samples, 10000):
                    raise HTTPException(429, "CAPTURE_POLICY_QUOTA_EXHAUSTED")
                redacted = redact(body.response, policy.never_capture_fields)
                try:
                    structured = json.loads(body.response)
                except (ValueError, RecursionError):
                    structured = None
                if redacted != body.response or (
                    structured is not None
                    and redact(structured, policy.never_capture_fields) != structured
                ):
                    raise HTTPException(422, "CACHE_RESPONSE_REQUIRES_APPLICATION_DEIDENTIFICATION")
                blob = json.dumps(
                    {"key_hash": key, "response": body.response}, ensure_ascii=False
                ).encode()
                if len(blob) > policy.max_payload_bytes:
                    raise HTTPException(413, "CACHE_RESPONSE_EXCEEDS_POLICY_BYTES")
                expiry = min(
                    datetime.now(UTC) + timedelta(seconds=body.ttl_seconds), policy.expires_at
                )
                object_ref = store.put(
                    body.project_id, blob, max(1, (body.ttl_seconds + 86399) // 86400)
                )
                row = CacheEntry(
                    project_id=body.project_id,
                    policy_id=body.policy_id,
                    key_hash=key,
                    object_ref=object_ref,
                    expires_at=expiry,
                )
                session.add(row)
                session.flush()
                session.add(
                    AuditEvent(
                        project_id=body.project_id,
                        actor=auth["actor"],
                        action="cache_entry_created",
                        details={
                            "cache_id": str(row.id),
                            "policy_id": str(body.policy_id),
                            "bytes": len(blob),
                            "expires_at": expiry.isoformat(),
                        },
                    )
                )
            committed = True
            return {
                "id": str(row.id),
                "expires_at": expiry.isoformat(),
                "content": "encrypted",
                "semantics": "application_authorized_immutable_or_invalidated",
            }
        finally:
            if object_ref and not committed:
                from cutback.server.campaign_content import discard_unreferenced

                discard_unreferenced(sessions, store, body.project_id, [object_ref])

    @app.get("/v1/projects/{project_id}/cache")
    def entries(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "policy_id": str(row.policy_id),
                    "expires_at": row.expires_at.isoformat(),
                }
                for row in session.scalars(
                    select(CacheEntry)
                    .where(CacheEntry.project_id == project_id)
                    .order_by(CacheEntry.expires_at)
                    .limit(100)
                )
            ]

    @app.delete("/v1/projects/{project_id}/cache/{cache_id}")
    def invalidate(project_id: UUID, cache_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(CacheEntry, cache_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "CACHE_NOT_FOUND")
            require_store(app).delete(project_id, row.object_ref)
            session.delete(row)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="cache_entry_invalidated",
                    details={"cache_id": str(cache_id)},
                )
            )
        return {"status": "invalidated"}


def expire_cache(sessions, store, *, now=None):
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        rows = session.scalars(
            select(CacheEntry)
            .where(CacheEntry.expires_at <= now)
            .order_by(CacheEntry.expires_at)
            .with_for_update(skip_locked=True)
            .limit(1000)
        ).all()
        for row in rows:
            store.delete(row.project_id, row.object_ref)
            session.execute(delete(CacheEntry).where(CacheEntry.id == row.id))
        return {"cache_entries_expired": len(rows)}
