"""Current scoped encrypted source policies; metadata never grants content authority."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from cutback.core.policy_execution import PolicyExecutionRef
from cutback.evals.datasets import digest
from cutback.evals.policy_artifacts import decode_policy_bundle, prepare_policy_payload
from cutback.evals.policy_models import policy_descriptors
from cutback.server.campaign_content import current_policy
from cutback.server.content_policy import policy_content
from cutback.server.db import (
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    Environment,
    PayloadSample,
)

KIND = "source_improvement_policy_v1"
MAX_BUNDLE_BYTES = 6 * 512000


def dataset_scope(session, project, dataset_id, source):
    dataset = session.get(DatasetRecord, UUID(str(dataset_id)), populate_existing=True)
    if (
        not dataset
        or dataset.project_id != project
        or dataset.workflow_id != source.workflow
        or dataset.provenance.get("deleted")
    ):
        raise ValueError("POLICY_DATASET_EXPIRED_OR_OUT_OF_SCOPE")
    provenance = dataset.provenance
    environment = session.scalar(
        select(Environment).where(
            Environment.project_id == project,
            Environment.name == provenance.get("environment"),
        )
    )
    if not environment:
        raise ValueError("POLICY_CURRENT_ENVIRONMENT_REQUIRED")
    try:
        policy_id = UUID(provenance["policy_id"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("POLICY_DATASET_CAPTURE_SCOPE_REQUIRED") from None
    policy = current_policy(session, project, policy_id, environment.name, source.workflow)
    if "openai" not in policy.allowed_providers:
        raise ValueError("POLICY_NATIVE_VENDOR_OUTSIDE_CURRENT_RIGHTS")
    return dataset, environment, policy_id, policy


def dataset_expiry(session, dataset, policy_id):
    rows = session.execute(
        select(DatasetItem, PayloadSample)
        .outerjoin(PayloadSample, DatasetItem.payload_id == PayloadSample.id)
        .where(DatasetItem.dataset_id == dataset.id)
        .limit(1025)
    ).all()
    if not 1 <= len(rows) <= 1024 or any(
        not sample
        or sample.project_id != dataset.project_id
        or sample.policy_id != policy_id
        or sample.expires_at <= datetime.now(UTC)
        or sample.metadata_safe.get("deleted")
        for _, sample in rows
    ):
        raise ValueError("POLICY_CURRENT_BOUNDED_DATASET_CONTENT_REQUIRED")
    return min(sample.expires_at for _, sample in rows)


def metadata(source, dataset_id, environment_id, key):
    # Source/file hashes are source bindings, never unsalted private policy hashes.
    return {
        "kind": KIND,
        "dataset_id": str(dataset_id),
        "environment_id": str(environment_id),
        "source": source.model_dump(mode="json", exclude={"policy_sha256"}),
        "key": key,
    }


def put_policy(session, store, project, dataset_id, source, key, value, identifier, refs):
    """Caller must establish current admin authority and hold the preparation lock."""
    from cutback.server.evaluation_api import policy_object_count

    if source.async_consumer and value.cache_execution is not None:
        raise ValueError("POLICY_SOURCE_ASYNC_CACHE_RUNTIME_UNQUALIFIED")
    dataset, environment, policy_id, policy = dataset_scope(session, project, dataset_id, source)
    expiry = dataset_expiry(session, dataset, policy_id)
    if value.workflow != source.workflow or value.max_input_bytes != source.max_input_bytes:
        raise ValueError("POLICY_UPLOAD_SOURCE_FRAME_CHANGED")
    current = session.get(
        CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True
    )
    if not current or current.policy != policy.model_dump(mode="json") or not current.enabled:
        raise ValueError("POLICY_CAPTURE_RIGHTS_CHANGED")
    expected = metadata(source, dataset_id, environment.id, key)
    previous = session.get(PayloadSample, identifier, populate_existing=True)
    if previous is not None:
        if (
            previous.project_id != project
            or previous.policy_id != policy_id
            or previous.metadata_safe != expected
            or previous.expires_at <= datetime.now(UTC)
        ):
            raise ValueError("POLICY_UPLOAD_IDENTITY_CHANGED_OR_EXPIRED")
        raw = read_payload(store, project, previous, policy)
        from cutback.evals.policy_artifacts import PolicyPayload

        if PolicyPayload.model_validate(raw).policy != value:
            raise ValueError("POLICY_UPLOAD_IDENTITY_CHANGED_OR_EXPIRED")
        return PolicyExecutionRef(
            key=key,
            payload_id=identifier,
            payload_sha256=previous.content_hash,
            policy_sha256=value.policy_sha256,
            cache_enabled=value.cache_execution is not None,
            cache_operation_upper_usd=value.cache_execution.operation_cost_upper_usd
            if value.cache_execution
            else None,
        )
    if policy_object_count(session, policy_id) >= min(policy.max_samples, 10000):
        raise ValueError("POLICY_SHARED_CONTENT_QUOTA_EXHAUSTED")
    raw, reference = prepare_policy_payload(key, identifier, value)
    clean, blob = policy_content(raw, policy, preserve_semantics=True)
    if digest(clean) != reference.payload_sha256 or len(blob) > 512000:
        raise ValueError("POLICY_ENCRYPTED_CONTENT_BOUND_OR_SEMANTICS_CHANGED")
    object_ref = store.put(project, blob, policy.retention_days)
    refs.append(object_ref)
    session.add(
        PayloadSample(
            id=identifier,
            project_id=project,
            policy_id=policy_id,
            object_ref=object_ref,
            content_hash=reference.payload_sha256,
            expires_at=min(
                expiry, policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
            ),
            metadata_safe=expected,
        )
    )
    session.flush()
    return reference


def read_payload(store, project, sample, policy):
    try:
        raw = store.get(project, sample.object_ref)
        if len(raw) > min(policy.max_payload_bytes, 512000):
            raise ValueError
        value = json.loads(raw)
        if type(value) is not dict or digest(value) != sample.content_hash:
            raise ValueError
    except Exception:
        raise ValueError("POLICY_PRIVATE_CONTENT_UNAVAILABLE_OR_CHANGED") from None
    policy_content(value, policy, preserve_semantics=True)
    return value


def execution_payloads(session, store, project, execution, dataset_id, *, read=True):
    dataset, environment, policy_id, policy = dataset_scope(
        session, project, dataset_id, execution.source
    )
    dataset_expiry(session, dataset, policy_id)
    if execution.transport == "scripted_test_only" and (
        dataset.provenance.get("source_type") != "synthetic_fixture"
        or policy.rights_basis != "synthetic"
    ):
        raise ValueError("POLICY_SCRIPTED_EXECUTION_REQUIRES_SYNTHETIC_DATA_AND_RIGHTS")
    if execution.phase == "training":
        from cutback.server.routing_service import check_training_scope

        check_training_scope(session, project, dataset, execution.max_cases)
    payloads = {}
    remaining = MAX_BUNDLE_BYTES
    for reference in [execution.reference, *execution.candidates]:
        sample = session.get(PayloadSample, reference.payload_id, populate_existing=True)
        if (
            not sample
            or sample.project_id != project
            or sample.policy_id != policy_id
            or sample.content_hash != reference.payload_sha256
            or sample.expires_at <= datetime.now(UTC)
            or sample.metadata_safe
            != metadata(execution.source, dataset_id, environment.id, reference.key)
        ):
            raise ValueError("POLICY_CURRENT_ENCRYPTED_SOURCE_BINDING_REQUIRED")
        if read:
            raw = read_payload(store, project, sample, policy)
            remaining -= len(json.dumps(raw, ensure_ascii=True).encode())
            if remaining < 0:
                raise ValueError("POLICY_PRIVATE_BUNDLE_READ_BOUND")
            payloads[reference.payload_id] = raw
    if read:
        decode_policy_bundle(payloads, execution)
        return payloads
    return None


def validate_binding(session, store, project, plan, dataset_id, *, read=True):
    execution = plan.source_policy_execution
    if execution is None:
        return None
    dataset, _, _, _ = dataset_scope(session, project, dataset_id, execution.source)
    if dataset.immutable_hash != plan.dataset_hash:
        raise ValueError("POLICY_FROZEN_DATASET_CHANGED")
    payloads = execution_payloads(session, store, project, execution, dataset_id, read=read)
    if read:
        policies = decode_policy_bundle(payloads, execution)
        descriptions = policy_descriptors(execution, policies)
        if any(descriptions.get(value.key) != value for value in [plan.baseline, *plan.candidates]):
            raise ValueError("POLICY_MODELS_COMPARISON_DESCRIPTOR_CHANGED")
    return payloads


def reserve_training(session, run, environment_id, plan_sha256):
    from cutback.server.db import ExperimentPlanRecord
    from cutback.server.principals import principal_role

    planned = session.get(ExperimentPlanRecord, run.plan_id, populate_existing=True)
    environment = session.get(Environment, environment_id, populate_existing=True)
    dataset = session.get(DatasetRecord, planned.dataset_id) if planned else None
    public = planned.specification.get("public_specification", {}) if planned else {}
    if (
        not planned
        or planned.project_id != run.project_id
        or planned.approval_status != "consumed"
        or planned.specification.get("plan_hash") != plan_sha256
        or public.get("evaluator_version") != "deterministic-v19"
        or public.get("split") != "train"
        or public.get("source_policy_execution", {}).get("phase") != "training"
        or not environment
        or environment.project_id != run.project_id
        or not dataset
        or dataset.project_id != run.project_id
        or public.get("dataset_hash") != dataset.immutable_hash
        or public.get("workflow") != dataset.workflow_id
        or environment.name != dataset.provenance.get("environment")
        or principal_role(session, run.project_id, planned.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("POLICY_TRAINING_EXACT_APPROVED_SCOPE_REQUIRED")
