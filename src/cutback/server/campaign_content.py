"""Encrypted campaign payloads, shared rights/quotas and uncertain-commit cleanup."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, text

from cutback.evals.datasets import CapturePolicy, digest
from cutback.server.content_policy import policy_content, require_payloads_enabled
from cutback.server.db import CapturePolicyRecord, ExperimentPlanRecord, PayloadSample


def discard_unreferenced(sessions, store, project, refs, *, policy_id=None):
    """After an uncertain commit, preserve every referenced or unreadable object."""
    for ref in refs:
        try:
            from cutback.server.enterprise_db import CacheEntry

            with sessions() as reader:
                if policy_id is not None:
                    # Cache writers hold this row until their transaction settles.
                    # A plain MVCC read can otherwise see the old pointer while
                    # a COMMIT acknowledgement is still uncertain. Allow a brief
                    # concurrent request to settle; a longer lock keeps ciphertext
                    # for operator reconciliation instead of risking data loss.
                    reader.execute(text("SET LOCAL lock_timeout = '250ms'"))
                    reader.scalar(
                        select(CapturePolicyRecord)
                        .where(
                            CapturePolicyRecord.id == policy_id,
                            CapturePolicyRecord.project_id == project,
                        )
                        .with_for_update()
                    )
                payload = reader.scalar(
                    select(PayloadSample.id)
                    .where(
                        PayloadSample.project_id == project,
                        or_(
                            PayloadSample.object_ref == ref, PayloadSample.native_profile_ref == ref
                        ),
                    )
                    .limit(1)
                )
                plan = reader.scalar(
                    select(ExperimentPlanRecord.id)
                    .where(
                        ExperimentPlanRecord.project_id == project,
                        ExperimentPlanRecord.specification["artifact_ref"].as_string() == ref,
                    )
                    .limit(1)
                )
                cache = reader.scalar(
                    select(CacheEntry.id)
                    .where(CacheEntry.project_id == project, CacheEntry.object_ref == ref)
                    .limit(1)
                )
            if payload is None and plan is None and cache is None:
                store.delete(project, ref)
        except Exception:
            pass  # Keep an uncertain reference for offline orphan reconciliation.


@contextmanager
def content_transaction(sessions, store, project):
    refs: list[str] = []
    try:
        with sessions.begin() as session:
            yield session, refs
    except BaseException:
        # A commit acknowledgement can be lost. Delete only after a fresh read
        # confirms neither payload nor exact-plan records reference this object.
        discard_unreferenced(sessions, store, project, refs)
        raise


def current_policy(session, project, policy_id, environment, workflow):
    require_payloads_enabled()
    row = session.get(CapturePolicyRecord, UUID(str(policy_id)), populate_existing=True)
    if not row or row.project_id != project or not row.enabled:
        raise ValueError("CAMPAIGN_CAPTURE_POLICY_REVOKED_OR_OUT_OF_SCOPE")
    policy = CapturePolicy.model_validate(row.policy)
    if not policy.permits(environment, workflow):
        raise ValueError("CAMPAIGN_CAPTURE_POLICY_EXPIRED_OR_OUT_OF_SCOPE")
    return policy


def put_payload(session, store, parent, value, kind, refs, policy):
    from cutback.server.evaluation_api import policy_object_count

    policy_row = session.get(
        CapturePolicyRecord, parent.policy_id, with_for_update=True, populate_existing=True
    )
    if (
        not policy_row
        or policy_row.project_id != parent.project_id
        or not policy_row.enabled
        or policy_row.policy != policy.model_dump(mode="json")
        or policy.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("CAMPAIGN_CONTENT_POLICY_CHANGED_OR_EXPIRED")
    if policy_object_count(session, parent.policy_id) >= policy.max_samples:
        raise ValueError("CAMPAIGN_CONTENT_POLICY_QUOTA_EXHAUSTED")
    wrapper = {"content_nonce": str(uuid4()), "value": value}
    clean, blob = policy_content(wrapper, policy, preserve_semantics=True)
    ref = store.put(parent.project_id, blob, policy.retention_days)
    refs.append(ref)
    row = PayloadSample(
        project_id=parent.project_id,
        policy_id=parent.policy_id,
        object_ref=ref,
        content_hash=digest(clean),
        expires_at=min(
            parent.expires_at,
            policy.expires_at,
            datetime.now(UTC) + timedelta(days=policy.retention_days),
        ),
        metadata_safe={"kind": kind, "campaign_id": str(parent.id)},
    )
    session.add(row)
    session.flush()
    return row


def read_payload(session, store, parent, payload_id, policy):
    require_payloads_enabled()
    row = session.get(PayloadSample, UUID(str(payload_id)))
    if (
        not row
        or row.project_id != parent.project_id
        or row.policy_id != parent.policy_id
        or row.expires_at <= datetime.now(UTC)
        or row.metadata_safe.get("deleted")
        or row.metadata_safe.get("campaign_id") != str(parent.id)
    ):
        raise ValueError("CAMPAIGN_CONTENT_EXPIRED_OR_OUT_OF_SCOPE")
    try:
        raw = json.loads(store.get(parent.project_id, row.object_ref))
    except Exception:
        raise ValueError("CAMPAIGN_CONTENT_UNAVAILABLE") from None
    if digest(raw) != row.content_hash or set(raw) != {"content_nonce", "value"}:
        raise ValueError("CAMPAIGN_CONTENT_INTEGRITY_FAILURE")
    policy_content(raw, policy, preserve_semantics=True)
    return raw["value"], row.content_hash
