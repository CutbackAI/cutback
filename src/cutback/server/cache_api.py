"""Explicit encrypted, project-scoped immutable response cache; disabled by default."""

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import delete, select

from cutback.core.exact_cache import EXACT_CACHE_CONTRACT
from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import CapturePolicy, digest, redact
from cutback.server.db import AuditEvent, CapturePolicyRecord, Environment
from cutback.server.enterprise_db import CacheEntry


class CacheKey(StrictModel):
    contract: Literal["native-exact-cache-v1"] = EXACT_CACHE_CONTRACT
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

    @field_validator("immutable_or_safely_invalidated", mode="before")
    @classmethod
    def explicit_authority(cls, value):
        if value is not True:
            raise ValueError("CACHE_EXPLICIT_BOOLEAN_IMMUTABILITY_AUTHORITY_REQUIRED")
        return value


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
            mode="json", exclude={"contract"}
        )
    )


def lookup_cache(sessions, store, body: CacheKey, auth, scope):
    enabled()
    with sessions.begin() as session:
        auth = auth(session) if callable(auth) else auth
        policy = cache_policy(session, body, auth, scope)
        row = session.scalar(
            select(CacheEntry).where(
                CacheEntry.project_id == body.project_id,
                CacheEntry.key_hash == key_digest(body),
            )
        )
        if not row or row.expires_at <= datetime.now(UTC):
            return {"contract": EXACT_CACHE_CONTRACT, "hit": False, "response": None}
        try:
            value = json.loads(store.get(body.project_id, row.object_ref))
            if value["key_hash"] != row.key_hash:
                raise ValueError("CACHE_INTEGRITY_FAILURE")
        except (ValueError, OSError, KeyError, InvalidTag):
            return {
                "contract": EXACT_CACHE_CONTRACT,
                "hit": False,
                "response": None,
                "reason": "expired_or_unavailable",
            }
        raw = value["response"]
        try:
            structured = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            structured = None
        if redact(raw, policy.never_capture_fields) != raw or (
            structured is not None and redact(structured, policy.never_capture_fields) != structured
        ):
            raise HTTPException(403, "CACHE_CURRENT_CONTENT_POLICY_REQUIRES_INVALIDATION")
        return {
            "contract": EXACT_CACHE_CONTRACT,
            "hit": True,
            "response": value["response"],
            "expires_at": row.expires_at.isoformat(),
            "cache_id": str(row.id),
        }


def put_cache(sessions, store, body: CachePut, auth, scope):
    enabled()
    object_ref = None
    retired_ref = None
    committed = False
    try:
        with sessions.begin() as session:
            auth = auth(session) if callable(auth) else auth
            policy = cache_policy(session, body, auth, scope)
            if body.ttl_seconds > policy.retention_days * 86400:
                raise HTTPException(422, "CACHE_TTL_EXCEEDS_CAPTURE_POLICY")
            key = key_digest(body)
            existing = session.scalar(
                select(CacheEntry)
                .where(CacheEntry.project_id == body.project_id, CacheEntry.key_hash == key)
                .with_for_update()
            )
            if existing and existing.expires_at > datetime.now(UTC):
                raise HTTPException(409, "CACHE_KEY_IMMUTABLE_INVALIDATE_OR_CHANGE_REVISION")
            if existing:
                if existing.policy_id != body.policy_id:
                    raise HTTPException(409, "CACHE_ENTRY_POLICY_BINDING_CONFLICT")
                retired_ref = existing.object_ref
            from cutback.server.evaluation_api import policy_object_count

            if policy_object_count(session, body.policy_id) - int(existing is not None) >= min(
                policy.max_samples, 10000
            ):
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
            expiry = min(datetime.now(UTC) + timedelta(seconds=body.ttl_seconds), policy.expires_at)
            object_ref = store.put(
                body.project_id, blob, max(1, (body.ttl_seconds + 86399) // 86400)
            )
            if existing is None:
                row = CacheEntry(
                    project_id=body.project_id,
                    policy_id=body.policy_id,
                    key_hash=key,
                    object_ref=object_ref,
                    expires_at=expiry,
                )
            else:
                row = existing
                row.object_ref = object_ref
                row.expires_at = expiry
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=body.project_id,
                    actor=auth["actor"],
                    action="cache_entry_refreshed" if retired_ref else "cache_entry_created",
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
            "contract": EXACT_CACHE_CONTRACT,
            "id": str(row.id),
            "expires_at": expiry.isoformat(),
            "content": "encrypted",
            "semantics": "application_authorized_immutable_or_invalidated",
        }
    finally:
        refs = ([object_ref] if object_ref and not committed else []) + (
            [retired_ref] if retired_ref else []
        )
        if refs:
            from cutback.server.campaign_content import discard_unreferenced

            discard_unreferenced(sessions, store, body.project_id, refs, policy_id=body.policy_id)


def install_cache(app, sessions, authorize, scope):
    from cutback.server.evaluation_api import require_store

    @app.post("/v1/ingest/cache/lookup")
    def lookup(body: CacheKey, auth=Depends(authorize)):
        enabled()
        return lookup_cache(sessions, require_store(app), body, auth, scope)

    @app.post("/v1/ingest/cache/put", status_code=201)
    def put(body: CachePut, auth=Depends(authorize)):
        enabled()
        return put_cache(sessions, require_store(app), body, auth, scope)

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
        refs = []
        try:
            with sessions.begin() as session:
                row = session.get(CacheEntry, cache_id, with_for_update=True)
                if not row or row.project_id != project_id:
                    raise HTTPException(404, "CACHE_NOT_FOUND")
                refs.append(row.object_ref)
                session.delete(row)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="cache_entry_invalidated",
                        details={"cache_id": str(cache_id)},
                    )
                )
        finally:
            from cutback.server.campaign_content import discard_unreferenced

            discard_unreferenced(sessions, require_store(app), project_id, refs)
        return {"status": "invalidated"}


def expire_cache(sessions, store, *, now=None):
    now = now or datetime.now(UTC)
    refs = []
    try:
        with sessions.begin() as session:
            rows = session.scalars(
                select(CacheEntry)
                .where(CacheEntry.expires_at <= now)
                .order_by(CacheEntry.expires_at)
                .with_for_update(skip_locked=True)
                .limit(1000)
            ).all()
            for row in rows:
                refs.append((row.project_id, row.object_ref))
                session.execute(delete(CacheEntry).where(CacheEntry.id == row.id))
        return {"cache_entries_expired": len(rows)}
    finally:
        from cutback.server.campaign_content import discard_unreferenced

        for project, ref in refs:
            discard_unreferenced(sessions, store, project, [ref])
