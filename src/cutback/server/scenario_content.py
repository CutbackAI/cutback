"""Bounded, training-only scenario sources and namespaced encrypted artifacts."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from cutback.evals.datasets import Case, digest
from cutback.evals.scenario_generation import validate_sources
from cutback.server import criteria_content
from cutback.server.db import DatasetItem, DatasetRecord, PayloadSample
from cutback.server.lineage_db import DatasetFamily


def current_policy(session, row):
    try:
        return criteria_content.current_policy(session, row)
    except ValueError:
        raise ValueError("SCENARIO_CURRENT_SOURCE_POLICY_OR_VENDOR_UNAVAILABLE") from None


def put_payload(session, store, row, value, kind, refs, policy):
    return criteria_content.put_payload(
        session, store, row, value, kind, refs, policy, namespace="scenario"
    )


def read_payload(session, store, row, payload_id, policy, *, kind):
    return criteria_content.read_payload(
        session, store, row, payload_id, policy, kind=kind, namespace="scenario"
    )


def training_view(session, store, project, dataset, policy, *, limit=4):
    from cutback.server.content_policy import policy_content
    from cutback.server.dataset_lineage import inspect_exposure

    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("SCENARIO_TRAINING_VIEW_BOUND")
    families, receipt = inspect_exposure(session, project, dataset.id, "train")
    if receipt["complete"] is not True:
        raise ValueError("SCENARIO_REQUIRES_COMPLETE_TRAINING_LINEAGE")
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
        raise ValueError("SCENARIO_TRAINING_FAMILY_OVERLAPS_KNOWN_TEST_ASSIGNMENT")
    items = session.scalars(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset.id, DatasetItem.split == "train")
        .order_by(DatasetItem.id)
        .limit(limit)
    ).all()
    sources = {}
    total = 0
    for index, item in enumerate(items):
        sample = session.get(PayloadSample, item.payload_id)
        if (
            not sample
            or sample.project_id != project
            or str(sample.policy_id) != str(dataset.provenance["policy_id"])
            or sample.expires_at <= datetime.now(UTC)
            or sample.metadata_safe.get("deleted")
        ):
            raise ValueError("SCENARIO_SOURCE_PAYLOAD_EXPIRED_OR_OUT_OF_SCOPE")
        try:
            raw = store.get(project, sample.object_ref)
            total += len(raw)
            if len(raw) > min(policy.max_payload_bytes, 64000) or total > 256000:
                raise ValueError
            value = json.loads(raw)
            if digest(value) != sample.content_hash:
                raise ValueError
            policy_content(value, policy, preserve_semantics=True)
            case = Case.model_validate(value)
        except Exception:
            raise ValueError("SCENARIO_SOURCE_PAYLOAD_UNAVAILABLE_OR_CHANGED") from None
        sources["training-" + str(index + 1)] = case
    validate_sources(sources)
    return sources, [str(item.id) for item in items]
