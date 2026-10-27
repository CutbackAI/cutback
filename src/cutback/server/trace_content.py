"""Explicitly authorized encrypted trace content; never part of metadata ingestion."""

import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from cutback.core.models import Identifier, StrictModel
from cutback.core.traces import SpanID, TraceID
from cutback.evals.datasets import digest
from cutback.server.content_policy import policy_content
from cutback.server.db import AuditEvent, Environment, PayloadSample
from cutback.server.evaluation_api import (
    ContentPurpose,
    active_policy,
    policy_object_count,
    require_store,
)
from cutback.server.trace_db import ExecutionSpan, ExecutionTrace, TraceContent


class TraceContentInput(StrictModel):
    policy_id: UUID
    sample_id: UUID
    trace_id: TraceID
    span_id: SpanID
    provider: Literal["openai", "anthropic", "local"]
    input: str | None = Field(default=None, max_length=64000)
    output: str | None = Field(default=None, max_length=64000)
    provenance_ref: Identifier

    @model_validator(mode="after")
    def nonempty(self):
        if self.input is None and self.output is None:
            raise ValueError("CONTENT_REQUIRED")
        return self


def read_trace_content(app, session, project, link, actor, purpose):
    store = require_store(app)
    payload = session.get(PayloadSample, link.payload_id)
    if not payload or payload.project_id != project or payload.expires_at <= datetime.now(UTC):
        raise HTTPException(410, "TRACE_CONTENT_EXPIRED_OR_UNAVAILABLE")
    _, policy = active_policy(session, project, payload.policy_id)
    metadata = payload.metadata_safe
    if (
        not policy.permits(metadata["environment"], metadata["workflow"])
        or metadata["provider"] not in policy.allowed_providers
    ):
        raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
    try:
        blob = store.get(project, payload.object_ref)
        if len(blob) > policy.max_payload_bytes:
            raise ValueError("CONTENT_BOUND_CHANGED")
        value = json.loads(blob)
        if digest(value) != payload.content_hash:
            raise ValueError("INTEGRITY_FAILED")
        clean, _ = policy_content(value, policy)
    except (OSError, ValueError, InvalidTag):
        raise HTTPException(410, "TRACE_CONTENT_EXPIRED_OR_UNAVAILABLE") from None
    session.add(
        AuditEvent(
            project_id=project,
            actor=actor,
            action="trace_content_read",
            details={"trace_content_id": str(link.id), "purpose": purpose},
        )
    )
    return {
        "content": clean,
        "policy_id": str(payload.policy_id),
        "expires_at": payload.expires_at.isoformat(),
        "evidence": "captured_application_content_not_gold",
    }


def install_trace_content(app, sessions, authorize, scope):
    @app.post("/v1/ingest/trace-content", status_code=201)
    def capture(body: TraceContentInput, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        project = UUID(str(auth["project_id"]))
        scope(auth, project, ("ingest",))
        store = require_store(app)
        ref = None
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project, body.policy_id)
                environment = session.get(Environment, auth["environment_id"])
                if not environment or environment.project_id != project:
                    raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
                row = session.scalar(
                    select(ExecutionSpan)
                    .join(ExecutionTrace)
                    .where(
                        ExecutionTrace.project_id == project,
                        ExecutionTrace.environment_id == environment.id,
                        ExecutionTrace.trace_id == body.trace_id,
                        ExecutionSpan.span_id == body.span_id,
                    )
                    .with_for_update()
                )
                if not row:
                    raise HTTPException(404, "TRACE_SPAN_NOT_FOUND")
                workflow = row.metadata_record.get("workflow")
                if (
                    not policy.permits(environment.name, workflow)
                    or body.provider not in policy.allowed_providers
                ):
                    raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                if body.sample_id.int / (2**128) >= policy.sample_rate:
                    raise HTTPException(403, "SAMPLE_OUTSIDE_APPROVED_RATE")
                value = {
                    "input": body.input,
                    "output": body.output,
                    "provenance_ref": body.provenance_ref,
                }
                try:
                    clean, blob = policy_content(value, policy)
                except ValueError:
                    raise HTTPException(422, "TRACE_CONTENT_POLICY_REJECTED") from None
                existing = session.scalar(
                    select(TraceContent).where(
                        TraceContent.trace_record_id == row.trace_record_id,
                        TraceContent.span_id == body.span_id,
                    )
                )
                if existing:
                    payload = session.get(PayloadSample, existing.payload_id)
                    if (
                        payload.id == body.sample_id
                        and payload.policy_id == body.policy_id
                        and payload.expires_at > datetime.now(UTC)
                        and payload.content_hash == digest(clean)
                    ):
                        return {"status": "duplicate", "content_id": str(existing.id)}
                    raise HTTPException(409, "TRACE_CONTENT_IMMUTABLE_IDENTITY_CONFLICT")
                if session.get(PayloadSample, body.sample_id):
                    raise HTTPException(409, "CONTENT_SAMPLE_ID_ALREADY_USED")
                if policy_object_count(session, body.policy_id) >= policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                ref = store.put(project, blob, policy.retention_days)
                session.add(
                    PayloadSample(
                        id=body.sample_id,
                        project_id=project,
                        policy_id=body.policy_id,
                        object_ref=ref,
                        content_hash=digest(clean),
                        expires_at=min(
                            policy.expires_at,
                            datetime.now(UTC) + timedelta(days=policy.retention_days),
                        ),
                        metadata_safe={
                            "kind": "workflow_span_content",
                            "environment": environment.name,
                            "workflow": workflow,
                            "provider": body.provider,
                            "trace_id": body.trace_id,
                            "span_id": body.span_id,
                        },
                    )
                )
                session.flush()
                link = TraceContent(
                    id=uuid4(),
                    project_id=project,
                    environment_id=environment.id,
                    trace_record_id=row.trace_record_id,
                    span_id=body.span_id,
                    payload_id=body.sample_id,
                )
                session.add(link)
                session.add(
                    AuditEvent(
                        project_id=project,
                        actor=auth["actor"],
                        action="trace_content_captured",
                        details={
                            "trace_content_id": str(link.id),
                            "policy_id": str(body.policy_id),
                        },
                    )
                )
            return {
                "status": "stored",
                "content_id": str(link.id),
                "content": "encrypted_separate_store",
            }
        except BaseException:
            if ref:
                store.delete(project, ref)
            raise

    @app.post("/v1/projects/{project_id}/traces/{trace_record_id}/spans/{span_id}/content")
    def read(
        project_id: UUID,
        trace_record_id: UUID,
        span_id: SpanID,
        body: ContentPurpose,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            link = session.scalar(
                select(TraceContent).where(
                    TraceContent.project_id == project_id,
                    TraceContent.trace_record_id == trace_record_id,
                    TraceContent.span_id == span_id,
                )
            )
            if not link:
                raise HTTPException(404, "TRACE_CONTENT_NOT_FOUND")
            return read_trace_content(app, session, project_id, link, auth["actor"], body.purpose)
