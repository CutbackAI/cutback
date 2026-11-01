"""Scoped, idempotent metadata intake and bounded inspection, without inference."""

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from typing import Any
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from cutback.core.policy_activity import (
    PolicyRuntimeBatch,
    PolicyRuntimeEvent,
    PolicyRuntimeScope,
    PolicyRuntimeServiceQuery,
    PolicyRuntimeServices,
    PolicyRuntimeSummary,
)
from cutback.server.billing import meter_ingestion
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import Environment
from cutback.server.policy_activity_db import PolicyActivityRecord
from cutback.server.traces import fingerprint


def ingest(session, project, environment, body):
    if project != body.project_id or any(
        event.environment != environment.name for event in body.events
    ):
        raise HTTPException(403, "POLICY_ACTIVITY_PROJECT_AND_ENVIRONMENT_SCOPE_REQUIRED")
    lock_lineage(session, project)
    requested: dict[UUID, tuple[PolicyRuntimeEvent, str, dict[str, Any]]] = {}
    for event in body.events:
        record = event.model_dump(mode="json")
        digest = fingerprint(record)
        if event.event_id in requested and requested[event.event_id][1] != digest:
            raise HTTPException(409, "POLICY_ACTIVITY_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
        requested[event.event_id] = (event, digest, record)
    records = [
        dict(
            id=uuid4(),
            project_id=project,
            environment_id=environment.id,
            event_id=event.event_id,
            workflow=event.workflow,
            service_name=event.service_name,
            source_commit=event.source_commit,
            call_site_id=event.call_site_id,
            policy_sha256=event.policy_sha256,
            started_ns=event.started_ns,
            fingerprint=digest,
            metadata_record=record,
        )
        for event, digest, record in requested.values()
    ]
    added = session.scalars(
        insert(PolicyActivityRecord)
        .values(records)
        .on_conflict_do_nothing(constraint="uq_policy_activity_event")
        .returning(PolicyActivityRecord.id)
    ).all()
    retained = dict(
        session.execute(
            select(PolicyActivityRecord.event_id, PolicyActivityRecord.fingerprint).where(
                PolicyActivityRecord.project_id == project,
                PolicyActivityRecord.environment_id == environment.id,
                PolicyActivityRecord.event_id.in_(list(requested)),
            )
        ).all()
    )
    if retained != {identifier: values[1] for identifier, values in requested.items()}:
        raise HTTPException(409, "POLICY_ACTIVITY_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
    meter_ingestion(session, project, len(added))
    return {
        "profile": "cutback.source-policy-runtime.v1",
        "accepted": len(added),
        "duplicate": len(body.events) - len(added),
    }


def inspect_activity(session, project, body):
    environment = session.scalar(
        select(Environment).where(
            Environment.project_id == project, Environment.name == body.environment
        )
    )
    if environment is None:
        raise HTTPException(404, "POLICY_ACTIVITY_ENVIRONMENT_NOT_FOUND")
    # Integer arithmetic avoids losing exact nanoseconds to a float conversion.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    def nanos(value):
        delta = value - epoch
        return (delta.days * 86400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000

    query = (
        select(PolicyActivityRecord)
        .where(
            PolicyActivityRecord.project_id == project,
            PolicyActivityRecord.environment_id == environment.id,
            *(
                getattr(PolicyActivityRecord, name) == getattr(body, name)
                for name in (
                    "service_name",
                    "workflow",
                    "call_site_id",
                    "source_commit",
                    "policy_sha256",
                )
            ),
            PolicyActivityRecord.started_ns >= nanos(body.window_start),
            PolicyActivityRecord.started_ns < nanos(body.window_end),
        )
        .order_by(PolicyActivityRecord.started_ns, PolicyActivityRecord.event_id)
        .limit(10001)
    )
    rows = session.scalars(query).all()
    truncated = len(rows) > 10000
    events = [PolicyRuntimeEvent.model_validate(row.metadata_record) for row in rows[:10000]]
    unknown_native = sum(event.native_sdk_invocations is None for event in events)
    unknown_cache = sum(event.cache_operations is None for event in events)
    known_native = sum(event.native_sdk_invocations or 0 for event in events)
    known_cache = sum(event.cache_operations or 0 for event in events)
    with localcontext(Context(prec=64)):
        cache_upper = sum(
            (event.cache_component_upper_usd or Decimal(0) for event in events), Decimal(0)
        )
    # Empty, truncated, or partly unknown evidence never becomes a complete cost/reference.
    incomplete = not events or truncated
    raw = [[str(row.event_id), row.fingerprint] for row in rows[:10000]]
    return PolicyRuntimeSummary(
        project_id=project,
        environment_id=environment.id,
        scope=body,
        retained_operations=len(events),
        truncated=truncated,
        raw_sha256=hashlib.sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest(),
        states=dict(Counter(event.state for event in events)),
        completions=dict(Counter(event.completion for event in events)),
        configured_operations=sum(event.cache_configured for event in events),
        requested_cache_operations=sum(event.cache_requested for event in events),
        known_native_sdk_invocations=known_native,
        native_sdk_invocations=None if unknown_native or incomplete else known_native,
        unknown_native_operations=unknown_native,
        known_cache_operations=known_cache,
        cache_operations=None if unknown_cache or incomplete else known_cache,
        unknown_cache_operations=unknown_cache,
        cache_component_upper_usd=None if unknown_cache or incomplete else cache_upper,
        cache_cost_basis_refs=sorted(
            {event.cache_cost_basis_ref for event in events if event.cache_cost_basis_ref}
        ),
    )


def install_policy_activity(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/policy-runtime/services", response_model=PolicyRuntimeServices
    )
    def services(project_id: UUID, body: PolicyRuntimeServiceQuery, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            if environment is None:
                raise HTTPException(404, "POLICY_ACTIVITY_ENVIRONMENT_NOT_FOUND")
            epoch = datetime(1970, 1, 1, tzinfo=UTC)

            def nanos(value):
                delta = value - epoch
                return (
                    delta.days * 86400 + delta.seconds
                ) * 1_000_000_000 + delta.microseconds * 1000

            values = session.scalars(
                select(PolicyActivityRecord.service_name)
                .where(
                    PolicyActivityRecord.project_id == project_id,
                    PolicyActivityRecord.environment_id == environment.id,
                    *(
                        getattr(PolicyActivityRecord, name) == getattr(body, name)
                        for name in ("workflow", "call_site_id", "source_commit", "policy_sha256")
                    ),
                    PolicyActivityRecord.started_ns >= nanos(body.window_start),
                    PolicyActivityRecord.started_ns < nanos(body.window_end),
                )
                .distinct()
                .order_by(PolicyActivityRecord.service_name)
                .limit(21)
            ).all()
            return PolicyRuntimeServices(
                project_id=project_id,
                environment_id=environment.id,
                scope=body,
                services=list(values[:20]),
                truncated=len(values) > 20,
            )

    @app.post("/v1/ingest/policy-runtime")
    def intake(body: PolicyRuntimeBatch, auth=Depends(authorize)):
        scope(auth, body.project_id, ("ingest",))
        with sessions.begin() as session:
            environment = session.get(Environment, auth.get("environment_id"))
            if not environment or environment.project_id != body.project_id:
                raise HTTPException(403, "POLICY_ACTIVITY_ENVIRONMENT_SCOPE_REQUIRED")
            return ingest(session, body.project_id, environment, body)

    @app.post(
        "/v1/projects/{project_id}/policy-runtime/inspect", response_model=PolicyRuntimeSummary
    )
    def inspect(project_id: UUID, body: PolicyRuntimeScope, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return inspect_activity(session, project_id, body)
