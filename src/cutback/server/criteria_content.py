"""Current rights, bounded training-only reads and encrypted criteria artifacts."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from cutback.evals.datasets import CapturePolicy, digest
from cutback.server.content_policy import policy_content, require_payloads_enabled
from cutback.server.db import (
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    Environment,
    PayloadSample,
)
from cutback.server.lineage_db import DatasetFamily


def current_policy(session, row):
    require_payloads_enabled()
    dataset = session.get(DatasetRecord, row.dataset_id, populate_existing=True)
    environment = session.get(Environment, row.environment_id, populate_existing=True)
    captured = session.get(CapturePolicyRecord, row.capture_policy_id, populate_existing=True)
    if (
        not dataset
        or dataset.project_id != row.project_id
        or dataset.immutable_hash != row.source_dataset_sha256
        or not environment
        or environment.project_id != row.project_id
        or environment.name != dataset.provenance.get("environment")
        or not captured
        or captured.project_id != row.project_id
        or not captured.enabled
        or str(captured.id) != str(dataset.provenance.get("policy_id"))
        or row.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("CRITERIA_SOURCE_RIGHTS_REVOKED_CHANGED_OR_EXPIRED")
    policy = CapturePolicy.model_validate(captured.policy)
    if (
        not policy.permits(environment.name, dataset.workflow_id)
        or "openai" not in policy.allowed_providers
    ):
        raise ValueError("CRITERIA_GENERATOR_VENDOR_OR_WORKFLOW_NOT_AUTHORIZED")
    return dataset, policy


def training_view(session, store, project, dataset, policy, *, limit=8):
    from cutback.server.dataset_lineage import inspect_exposure

    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("CRITERIA_TRAINING_VIEW_BOUND")
    families, receipt = inspect_exposure(session, project, dataset.id, "train")
    if receipt["complete"] is not True:
        raise ValueError("CRITERIA_REQUIRES_COMPLETE_TRAINING_LINEAGE")
    # A different import cannot relabel a known holdout family as generator input.
    heldout = session.scalar(
        select(DatasetItem.id)
        .join(DatasetRecord, DatasetItem.dataset_id == DatasetRecord.id)
        .join(DatasetFamily, DatasetItem.lineage_group_id == DatasetFamily.id)
        .where(
            DatasetRecord.project_id == project,
            DatasetItem.split == "test",
            DatasetFamily.canonical_id.in_(families),
        )
        .limit(1)
    )
    if heldout:
        raise ValueError("CRITERIA_TRAINING_FAMILY_OVERLAPS_KNOWN_TEST_ASSIGNMENT")
    items = session.scalars(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset.id, DatasetItem.split == "train")
        .order_by(DatasetItem.id)
        .limit(limit)
    ).all()
    if not items:
        raise ValueError("CRITERIA_REQUIRES_PERMITTED_TRAINING_EXAMPLES")
    result = []
    for index, item in enumerate(items):
        sample = session.get(PayloadSample, item.payload_id)
        if (
            not sample
            or sample.project_id != project
            or str(sample.policy_id) != str(dataset.provenance["policy_id"])
            or sample.expires_at <= datetime.now(UTC)
            or sample.metadata_safe.get("deleted")
        ):
            raise ValueError("CRITERIA_TRAINING_PAYLOAD_EXPIRED_OR_OUT_OF_SCOPE")
        try:
            raw = store.get(project, sample.object_ref)
            if len(raw) > min(policy.max_payload_bytes, 512000):
                raise ValueError
            case = json.loads(raw)
        except Exception:
            raise ValueError("CRITERIA_TRAINING_PAYLOAD_UNAVAILABLE") from None
        if (
            digest(case) != sample.content_hash
            or not isinstance(case, dict)
            or not isinstance(case.get("input"), str)
            or case.get("gold") is None
        ):
            raise ValueError("CRITERIA_TRAINING_LABEL_OR_INTEGRITY_REQUIRED")
        policy_content(case, policy, preserve_semantics=True)
        if (
            len(case["input"].encode()) > 4096
            or len(json.dumps(case["gold"], ensure_ascii=False).encode()) > 2048
        ):
            raise ValueError("CRITERIA_SELECT_SMALLER_TRAINING_EXAMPLES")
        result.append(
            {"anchor": "training-" + str(index + 1), "input": case["input"], "gold": case["gold"]}
        )
    return result, [str(item.id) for item in items]


def put_payload(session, store, row, value, kind, refs, policy):
    from cutback.server.evaluation_api import policy_object_count

    policy_row = session.get(
        CapturePolicyRecord, row.capture_policy_id, with_for_update=True, populate_existing=True
    )
    if (
        not policy_row
        or not policy_row.enabled
        or policy_row.project_id != row.project_id
        or policy_row.policy != policy.model_dump(mode="json")
        or policy.expires_at <= datetime.now(UTC)
    ):
        raise ValueError("CRITERIA_CONTENT_POLICY_CHANGED_OR_EXPIRED")
    count = policy_object_count(session, row.capture_policy_id)
    if kind == "criteria_output" and row.state == "RUNNING" and row.output_payload_id is None:
        # Consume this exact live parent's durable output slot under the policy lock.
        count -= 1
    if count >= policy.max_samples:
        raise ValueError("CRITERIA_SHARED_CONTENT_QUOTA_EXHAUSTED")
    clean, blob = policy_content(
        {"content_nonce": str(uuid4()), "value": value}, policy, preserve_semantics=True
    )
    ref = store.put(row.project_id, blob, policy.retention_days)
    refs.append(ref)
    payload = PayloadSample(
        project_id=row.project_id,
        policy_id=row.capture_policy_id,
        object_ref=ref,
        content_hash=digest(clean),
        expires_at=min(
            row.expires_at,
            policy.expires_at,
            datetime.now(UTC) + timedelta(days=policy.retention_days),
        ),
        metadata_safe={"kind": kind, "criteria_id": str(row.id)},
    )
    session.add(payload)
    session.flush()
    return payload


def read_payload(session, store, row, payload_id, policy, *, kind):
    payload = session.get(PayloadSample, UUID(str(payload_id)))
    if (
        not payload
        or payload.project_id != row.project_id
        or payload.policy_id != row.capture_policy_id
        or payload.expires_at <= datetime.now(UTC)
        or payload.metadata_safe.get("deleted")
        or payload.metadata_safe.get("criteria_id") != str(row.id)
        or payload.metadata_safe.get("kind") != kind
    ):
        raise ValueError("CRITERIA_CONTENT_EXPIRED_OR_OUT_OF_SCOPE")
    try:
        blob = store.get(row.project_id, payload.object_ref)
        if len(blob) > min(policy.max_payload_bytes, 1048576):
            raise ValueError("CRITERIA_CONTENT_BYTE_BOUND")
        raw = json.loads(blob)
    except Exception:
        raise ValueError("CRITERIA_CONTENT_UNAVAILABLE") from None
    if (
        not isinstance(raw, dict)
        or set(raw) != {"content_nonce", "value"}
        or digest(raw) != payload.content_hash
    ):
        raise ValueError("CRITERIA_CONTENT_INTEGRITY_FAILURE")
    policy_content(raw, policy, preserve_semantics=True)
    return raw["value"], payload.content_hash
