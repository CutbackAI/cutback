"""Scoped upload authority and canonical encrypted cases shared by HTTP and worker."""

import json
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import select

from cutback.evals.datasets import Case, digest
from cutback.server.content_policy import policy_content
from cutback.server.db import PayloadSample
from cutback.server.principals import principal_role
from cutback.server.semantic_cache_service import authority, namespace
from cutback.server.semantic_upload_db import SemanticUploadCase
from cutback.server.semantic_upload_models import SemanticUploadPrepare


def upload_authority(session, row):
    if row.expires_at <= datetime.now(UTC) or row.approval_hash != digest(row.specification):
        raise ValueError("SEMANTIC_UPLOAD_EXPIRED_OR_CHANGED")
    ns = namespace(session, row.project_id, row.namespace_id)
    spec, policy, _ = authority(session, ns)
    prepared = SemanticUploadPrepare.model_validate(row.specification["upload"])
    if (
        row.specification["namespace_hash"] != ns.specification_hash
        or row.specification["capture_policy_hash"] != digest(policy.model_dump(mode="json"))
        or prepared.metadata.policy_id != row.policy_id
        or prepared.metadata.workflow != spec.workflow
        or prepared.metadata.environment != spec.environment
        or principal_role(session, row.project_id, row.created_by)
        not in {"analyst", "admin", "project_admin"}
    ):
        raise ValueError("SEMANTIC_UPLOAD_CURRENT_SCOPE_OR_AUTHORITY_DENIED")
    return ns, policy, prepared


def staged_cases(session, store, row, *, deadline, check=None):
    """Read the complete frozen sequence within independent byte/time bounds."""
    records = session.scalars(
        select(SemanticUploadCase)
        .where(
            SemanticUploadCase.upload_id == row.id, SemanticUploadCase.project_id == row.project_id
        )
        .order_by(SemanticUploadCase.ordinal)
        .limit(513)
    ).all()
    if len(records) != row.expected_cases or row.received_cases != row.expected_cases:
        raise ValueError("SEMANTIC_UPLOAD_INCOMPLETE")
    cases, payloads, total = [], {}, 0
    for ordinal, record in enumerate(records):
        if monotonic() >= deadline:
            raise ValueError("SEMANTIC_UPLOAD_FINALIZATION_TIME_BOUND")
        if check:
            check()
        _, policy, _ = upload_authority(session, row)
        payload = session.get(PayloadSample, record.payload_id, populate_existing=True)
        if (
            record.ordinal != ordinal
            or not payload
            or payload.project_id != row.project_id
            or payload.policy_id != row.policy_id
            or payload.expires_at <= datetime.now(UTC)
            or payload.content_hash != record.content_hash
            or payload.metadata_safe.get("deleted")
        ):
            raise ValueError("SEMANTIC_UPLOAD_STAGED_PAYLOAD_CHANGED_OR_EXPIRED")
        try:
            raw = store.get(row.project_id, payload.object_ref)
        except Exception:
            raise ValueError("SEMANTIC_UPLOAD_STAGED_CONTENT_UNAVAILABLE") from None
        total += len(raw)
        if len(raw) != record.byte_count or total > row.max_bytes:
            raise ValueError("SEMANTIC_UPLOAD_STAGED_BYTES_MISMATCH")
        try:
            case = Case.model_validate(json.loads(raw))
        except (ValueError, TypeError):
            raise ValueError("SEMANTIC_UPLOAD_STAGED_CASE_INVALID") from None
        if digest(case.model_dump(mode="json")) != record.content_hash or case.id in payloads:
            raise ValueError("SEMANTIC_UPLOAD_STAGED_HASH_OR_CASE_ID_CONFLICT")
        policy_content(case.model_dump(mode="json"), policy, preserve_semantics=True)
        cases.append(case)
        payloads[case.id] = payload.id
    if (
        total != row.received_bytes
        or digest([case.model_dump(mode="json") for case in cases])
        != row.specification["upload"]["expected_cases_sha256"]
    ):
        raise ValueError("SEMANTIC_UPLOAD_COMPLETE_SEQUENCE_HASH_MISMATCH")
    if monotonic() >= deadline:
        raise ValueError("SEMANTIC_UPLOAD_FINALIZATION_TIME_BOUND")
    return cases, payloads
