"""Encrypted common-program content with shared capture quotas and explicit ownership."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cutback.evals.datasets import digest
from cutback.server.content_policy import policy_content
from cutback.server.db import CapturePolicyRecord, PayloadSample
from cutback.server.policy_program_service import link_payload, require_link
from cutback.server.routing_service import _payload


def put_content(
    session, store, parent, value, kind, policy, refs, *, identifier=None, derivation=None
):
    from cutback.server.evaluation_api import policy_object_count

    if kind not in {"envelope", "generation_input", "generation_output", "fit"}:
        raise ValueError("POLICY_PROGRAM_PRIVATE_CONTENT_KIND_REQUIRED")
    identifier = identifier or uuid4()
    capture = session.get(
        CapturePolicyRecord, parent.policy_id, with_for_update=True, populate_existing=True
    )
    if (
        not capture
        or capture.project_id != parent.project_id
        or not capture.enabled
        or capture.policy != policy.model_dump(mode="json")
        or policy.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("POLICY_PROGRAM_CURRENT_CONTENT_POLICY_REQUIRED")
    metadata = {
        "kind": "source_policy_program_envelope"
        if kind == "envelope"
        else "source_policy_program_content",
        "source_policy_program_id": str(parent.id),
        "content_kind": kind,
    }
    if kind != "envelope":
        metadata["program_sha256"] = parent.approval_sha256
    previous = session.get(PayloadSample, identifier, populate_existing=True)
    if previous is not None:
        if (
            previous.project_id != parent.project_id
            or previous.policy_id != parent.policy_id
            or previous.metadata_safe != metadata
        ):
            raise ValueError("POLICY_PROGRAM_CONTENT_IDENTITY_CHANGED")
        _, raw = _payload(session, store, parent.project_id, parent.policy_id, identifier, policy)
        expected = {key: item for key, item in raw.items() if key != "content_nonce"}
        if expected != (value if kind == "envelope" else {"value": value}):
            raise ValueError("POLICY_PROGRAM_CONTENT_IDENTITY_CHANGED")
        if kind != "envelope":
            require_link(session, parent, identifier, previous.content_hash, kinds={kind})
        return previous
    if policy_object_count(session, parent.policy_id) >= min(policy.max_samples, 10000):
        raise ValueError("POLICY_PROGRAM_SHARED_CONTENT_QUOTA_EXHAUSTED")
    raw = {"content_nonce": str(uuid4()), **(value if kind == "envelope" else {"value": value})}
    clean, blob = policy_content(raw, policy, preserve_semantics=True)
    if len(blob) > 512000:
        raise ValueError("POLICY_PROGRAM_PRIVATE_CONTENT_BYTE_BOUND")
    object_ref = store.put(parent.project_id, blob, policy.retention_days)
    refs.append(object_ref)
    row = PayloadSample(
        id=identifier,
        project_id=parent.project_id,
        policy_id=parent.policy_id,
        object_ref=object_ref,
        content_hash=digest(clean),
        expires_at=min(
            parent.expires_at,
            policy.expires_at,
            datetime.now(UTC) + timedelta(days=policy.retention_days),
        ),
        metadata_safe=metadata,
    )
    session.add(row)
    session.flush()
    if kind != "envelope":
        link_payload(session, parent, row, kind, derivation=derivation)
    return row


def read_content(session, store, parent, identifier, content_sha256, policy, *, kind):
    require_link(session, parent, identifier, content_sha256, kinds={kind})
    sample, raw = _payload(session, store, parent.project_id, parent.policy_id, identifier, policy)
    if sample.metadata_safe != {
        "kind": "source_policy_program_content",
        "source_policy_program_id": str(parent.id),
        "content_kind": kind,
        "program_sha256": parent.approval_sha256,
    } or set(raw) != {"content_nonce", "value"}:
        raise ValueError("POLICY_PROGRAM_CURRENT_OWNED_CONTENT_REQUIRED")
    return raw["value"]
