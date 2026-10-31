"""Bounded analysis of authorized encrypted SDK request profiles; no inference."""

import os
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, or_, select, true

from cutback.core.models import LLMEvent
from cutback.core.native_request_profile import (
    NativeProfileObservation,
    NativeRequestProfileSnapshot,
    summarize_native_profile,
)
from cutback.evals.datasets import digest
from cutback.server.db import AuditEvent, Environment, PayloadSample, StoredEvent
from cutback.server.evaluation_api import active_policy
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.principals import principal_role
from cutback.server.repository import canonical_attempt


def authority(session, row):
    if os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower() != "true":
        raise ValueError("NATIVE_PROFILE_CONTENT_DISABLED")
    _, policy = active_policy(session, row.project_id, row.policy_id)
    if (
        not policy.native_request_profile
        or policy.never_capture_fields
        or not policy.permits(row.environment, row.workflow)
        or "openai" not in policy.allowed_providers
        or digest(policy.model_dump(mode="json")) != row.capture_policy_sha256
        or principal_role(session, row.project_id, row.requester_actor)
        not in {"admin", "project_admin"}
        or row.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("NATIVE_PROFILE_CURRENT_AUTHORITY_REQUIRED")
    return policy


def public(row):
    expired = row.expires_at <= datetime.now(UTC)
    return {
        "id": str(row.id),
        "policy_id": str(row.policy_id),
        "environment": row.environment,
        "workflow": row.workflow,
        "window_start": row.window_start,
        "window_end": row.window_end,
        "state": "EXPIRED" if expired else row.state,
        "attempts": row.attempts,
        "max_observations": row.max_observations,
        "max_wall_seconds": row.max_wall_seconds,
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "failure_code": row.failure_code,
        "summary": None if expired else row.summary,
        "provider_calls": 0,
        "encrypted_storage_requests_may_be_billable": True,
    }


def recover_native_profiles(sessions, *, now=None):
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        rows = session.scalars(
            select(NativeProfileAnalysis)
            .where(
                or_(
                    (NativeProfileAnalysis.expires_at <= now)
                    & (NativeProfileAnalysis.state != "EXPIRED"),
                    (NativeProfileAnalysis.state == "RUNNING")
                    & (NativeProfileAnalysis.lease_until <= now),
                )
            )
            .order_by(NativeProfileAnalysis.expires_at, NativeProfileAnalysis.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            if row.expires_at <= now:
                # Erase the summary but preserve the identity against a second dispatch.
                row.state, row.summary, row.lease_until = "EXPIRED", None, None
                row.failure_code = "NATIVE_PROFILE_EXPIRED_NO_REPLAY"
                row.completed_at = now
            elif row.state == "RUNNING":
                row.state = "FAILED"
                row.summary = None
                row.failure_code = "NATIVE_PROFILE_UNCERTAIN_STORAGE_READ_NO_REPLAY"
                row.completed_at = now
                row.lease_until = None
        return {"recovered_or_expired": len(rows), "provider_calls": 0}


def _event_clause(row):
    return (
        StoredEvent.project_id == row.project_id,
        Environment.id == StoredEvent.environment_id,
        Environment.name == row.environment,
        StoredEvent.workflow_id == row.workflow,
        StoredEvent.occurred_at >= row.window_start,
        StoredEvent.occurred_at < row.window_end,
        canonical_attempt(),
    )


def run_native_profile_one(sessions, store, *, only_analysis_id=None, clock=time.monotonic):
    """Claim once before storage I/O; never redispatch after an uncertain lease."""
    identifier = UUID(str(only_analysis_id)) if only_analysis_id else None
    recover_native_profiles(sessions)
    with sessions() as session:
        candidate = session.execute(
            select(NativeProfileAnalysis.id, NativeProfileAnalysis.policy_id)
            .where(
                NativeProfileAnalysis.state == "QUEUED",
                NativeProfileAnalysis.id == identifier if identifier else true(),
            )
            .order_by(NativeProfileAnalysis.created_at, NativeProfileAnalysis.id)
            .limit(1)
        ).first()
    if not candidate:
        return {"status": "IDLE", "provider_calls": 0}
    identifier, policy_id = candidate
    started = clock()
    try:
        with sessions.begin() as session:
            # Policy locks precede analysis locks, matching create/cancel and content reads.
            row = session.get(NativeProfileAnalysis, identifier)
            if not row:
                return {"status": "UNAVAILABLE", "provider_calls": 0}
            active_policy(session, row.project_id, policy_id)
            row = session.get(
                NativeProfileAnalysis, identifier, with_for_update=True, populate_existing=True
            )
            if row.state != "QUEUED" or row.attempts != 0:
                return {"status": "IDLE", "provider_calls": 0}
            policy = authority(session, row)
            row.state, row.attempts = "RUNNING", 1
            row.lease_until = datetime.now(UTC) + timedelta(seconds=row.max_wall_seconds + 60)
            session.add(
                AuditEvent(
                    project_id=row.project_id,
                    actor="native_profile_worker",
                    action="native_profile_analysis_started",
                    details={"analysis_id": str(identifier)},
                )
            )
        observations = []
        cursor = None
        seen = 0
        truncated = False
        content_expires_at = None
        while True:
            with sessions.begin() as session:
                row = session.get(NativeProfileAnalysis, identifier)
                if not row:
                    raise ValueError("NATIVE_PROFILE_ANALYSIS_REMOVED")
                policy = authority(session, row)
                row = session.get(
                    NativeProfileAnalysis, identifier, with_for_update=True, populate_existing=True
                )
                if row.state != "RUNNING" or clock() - started > row.max_wall_seconds:
                    raise ValueError("NATIVE_PROFILE_CANCELLED_OR_DEADLINE")
                query = (
                    select(PayloadSample, StoredEvent)
                    .join(
                        StoredEvent,
                        (
                            (StoredEvent.project_id == PayloadSample.project_id)
                            & (
                                StoredEvent.event_id
                                == PayloadSample.metadata_safe["event_id"]
                                .as_string()
                                .cast(StoredEvent.event_id.type)
                            )
                        ),
                    )
                    .join(Environment, Environment.id == StoredEvent.environment_id)
                    .where(
                        *_event_clause(row),
                        PayloadSample.policy_id == row.policy_id,
                        PayloadSample.native_profile_ref.is_not(None),
                        PayloadSample.expires_at > datetime.now(UTC),
                        PayloadSample.id > cursor if cursor else true(),
                    )
                    .order_by(PayloadSample.id)
                    .limit(1)
                )
                pair = session.execute(query).first()
                if pair is None:
                    break
                if seen >= row.max_observations:
                    truncated = True
                    break
                sample, event = pair
                content_expires_at = (
                    min(content_expires_at, sample.expires_at)
                    if content_expires_at is not None
                    else sample.expires_at
                )
                if sample.metadata_safe.get("provider") != "openai":
                    raise ValueError("NATIVE_PROFILE_EVENT_SCOPE_CONFLICT")
                native = LLMEvent.model_validate(event.event)
                if native.environment != row.environment or native.workflow_id != row.workflow:
                    raise ValueError("NATIVE_PROFILE_EVENT_SCOPE_CONFLICT")
                # The policy, current principal, cancellation and deadline are checked for
                # each encrypted read while their relational authority remains locked.
                raw = store.get(row.project_id, sample.native_profile_ref)
                if len(raw) > min(policy.max_payload_bytes, 4096):
                    raise ValueError("NATIVE_PROFILE_OBJECT_TOO_LARGE")
                profile = NativeRequestProfileSnapshot.model_validate_json(raw)
                if digest(profile.model_dump(mode="json")) != sample.native_profile_sha256:
                    raise ValueError("NATIVE_PROFILE_CONTENT_HASH_CONFLICT")
                if native.provider != "openai" or native.sdk_surface != "responses.create":
                    raise ValueError("NATIVE_PROFILE_EVENT_SCOPE_CONFLICT")
                observations.append(
                    NativeProfileObservation(
                        event_id=event.event_id,
                        occurred_at=event.occurred_at,
                        duration_ms=native.duration_ms,
                        profile=profile,
                        provider_modeled_cost_usd=event.cost_usd,
                    )
                )
                cursor, seen = sample.id, seen + 1
        with sessions.begin() as session:
            row = session.get(NativeProfileAnalysis, identifier)
            if not row:
                raise ValueError("NATIVE_PROFILE_ANALYSIS_REMOVED")
            policy = authority(session, row)
            row = session.get(
                NativeProfileAnalysis, identifier, with_for_update=True, populate_existing=True
            )
            if (
                row.state != "RUNNING"
                or clock() - started > row.max_wall_seconds
                or content_expires_at is not None
                and content_expires_at <= datetime.now(UTC)
            ):
                raise ValueError("NATIVE_PROFILE_CANCELLED_OR_DEADLINE")
            # A bounded count never silently turns a larger retained stream into a census.
            ids = select(StoredEvent.id).join(Environment).where(*_event_clause(row)).limit(100001)
            metadata_count = session.scalar(select(func.count()).select_from(ids.subquery()))
            if metadata_count > 100000:
                metadata_count, truncated = None, True
            result = summarize_native_profile(
                observations,
                policy.native_request_profile,
                capture_policy_sha256=row.capture_policy_sha256,
                metadata_attempts=metadata_count,
                sample_rate=policy.sample_rate,
                truncated=truncated,
            )
            from cutback.server.native_profile_metadata import metadata_digest, retained_metadata

            metadata = retained_metadata(session, row)
            stable = metadata_count is not None and len(metadata) == metadata_count <= 1000
            result["metadata_evidence_sha256"] = metadata_digest(metadata) if stable else None
            if not stable:
                result["abstention_reasons"].append("METADATA_SNAPSHOT_INCOMPLETE_OR_CHANGED")
                result["cache_trial_eligible"] = False
            if content_expires_at is not None:
                row.expires_at = min(row.expires_at, content_expires_at)
            row.summary, row.state = result, "COMPLETED"
            row.completed_at, row.lease_until = datetime.now(UTC), None
            session.add(
                AuditEvent(
                    project_id=row.project_id,
                    actor="native_profile_worker",
                    action="native_profile_analysis_completed",
                    details={"analysis_id": str(identifier)},
                )
            )
        return {"status": "COMPLETED", "analysis_id": str(identifier), "provider_calls": 0}
    except Exception:
        # A failed storage read may already have incurred a charge. Keep it terminal.
        outcome = "UNAVAILABLE"
        with sessions.begin() as session:
            row = session.get(NativeProfileAnalysis, identifier, with_for_update=True)
            if row and row.state in {"QUEUED", "RUNNING"}:
                row.state, row.summary = "FAILED", None
                row.failure_code = "NATIVE_PROFILE_AUTHORITY_CONTENT_OR_DEADLINE_FAILED_NO_REPLAY"
                row.completed_at, row.lease_until = datetime.now(UTC), None
            if row:
                outcome = row.state
        return {"status": outcome, "analysis_id": str(identifier), "provider_calls": 0}
