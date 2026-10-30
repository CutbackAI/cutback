"""Persistent cross-version grouping; no raw input/customer-group content is retained."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy import exists, select, text, update
from sqlalchemy.dialects.postgresql import insert

from cutback.evals.datasets import digest, normalized_input
from cutback.server.db import (
    AuditEvent,
    DatasetItem,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
)
from cutback.server.lineage_db import DatasetAlias, DatasetExposure, DatasetFamily


class LineageViolation(ValueError):
    """Fixed diagnostics safe to expose without example/customer-group content."""


def dataset_version_hash(provenance, content_hash, assignment_hash):
    return digest(
        {
            "scheme": "cutback.dataset.lineage.v1",
            "content_hash": content_hash,
            "assignment_hash": assignment_hash,
            "extensions_hash": digest(
                {key: provenance[key] for key in provenance.get("provenance_extension_keys", [])}
            ),
        }
    )


def lineage_lock_key(project):
    return int.from_bytes(
        hashlib.sha256(b"cutback-lineage-v1:" + project.bytes).digest()[:8], "big", signed=True
    )


def lock_lineage(session, project):
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lineage_lock_key(project)})


def try_lock_lineage(session, project):
    """Use the same mutation lock without letting a busy project starve another."""
    return bool(
        session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lineage_lock_key(project)}
        )
    )


def require_unreserved_groups(session, project, families):
    if session.scalar(
        select(DatasetExposure.id)
        .join(DatasetFamily, DatasetExposure.family_id == DatasetFamily.id)
        .join(ExperimentRunRecord, DatasetExposure.run_id == ExperimentRunRecord.id)
        .where(
            DatasetExposure.project_id == project,
            DatasetFamily.canonical_id.in_(families),
            DatasetExposure.protected.is_(True),
            ExperimentRunRecord.status.in_(["QUEUED", "RUNNING"]),
        )
        .limit(1)
    ):
        raise LineageViolation("PROTECTED_TEST_GROUP_RESERVED_BY_ACTIVE_RUN")


def register_groups(session, project, cases, *, actor="dataset_lineage"):
    lock_lineage(session, project)
    if len({c.id for c in cases}) != len(cases):
        raise LineageViolation("DUPLICATE_CASE_IDENTITIES")
    tokens = {
        case.id: ["input:" + digest(normalized_input(case.input))]
        + (["customer:" + digest(case.group)] if case.group else [])
        + (["security-family:" + digest(case.security.family)] if case.security else [])
        for case in cases
    }
    all_tokens = {value for group in tokens.values() for value in group}
    known = dict(
        session.execute(
            select(DatasetAlias.token_hash, DatasetFamily.canonical_id)
            .join(DatasetFamily, DatasetAlias.family_id == DatasetFamily.id)
            .where(
                DatasetAlias.project_id == project,
                DatasetFamily.project_id == project,
                DatasetAlias.token_hash.in_(all_tokens),
            )
        ).all()
    )
    parents = {value: value for value in all_tokens}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)

    for token, family in known.items():
        symbol = "family:" + str(family)
        parents.setdefault(symbol, symbol)
        union(token, symbol)
    for case_tokens in tokens.values():
        for token in case_tokens[1:]:
            union(case_tokens[0], token)
    components: dict[str, set[str]] = {}
    for token in all_tokens:
        components.setdefault(find(token), set()).add(token)
    roots = {}
    new_families, new_aliases = [], []
    for key, values in components.items():
        families = {known[value] for value in values if value in known}
        root = min(families, key=str) if families else uuid4()
        roots[key] = root
        if not families:
            new_families.append(DatasetFamily(id=root, project_id=project, canonical_id=root))
        elif len(families) > 1:
            # Flatten all original family nodes; exposure IDs and old items remain immutable.
            session.execute(
                update(DatasetFamily)
                .where(
                    DatasetFamily.project_id == project, DatasetFamily.canonical_id.in_(families)
                )
                .values(canonical_id=root)
            )
            session.add(
                AuditEvent(
                    project_id=project,
                    actor=actor,
                    action="dataset_families_merged",
                    details={
                        "canonical_root_id": str(root),
                        "families_merged": len(families),
                        "basis": "normalized_input_or_declared_customer_group",
                    },
                )
            )
        for token in sorted(values):
            if token not in known:
                new_aliases.append(
                    DatasetAlias(project_id=project, token_hash=token, family_id=root)
                )
    session.add_all(new_families)
    session.flush()
    session.add_all(new_aliases)
    session.flush()
    return {case.id: roots[find(tokens[case.id][0])] for case in cases}


def _families(session, project, dataset_id, mode):
    dataset = session.get(DatasetRecord, dataset_id)
    if not dataset or dataset.project_id != project:
        raise LineageViolation("LINEAGE_DATASET_OUT_OF_SCOPE")
    items = session.execute(
        select(
            DatasetItem.lineage_group_id,
            DatasetItem.split,
            DatasetFamily.canonical_id,
            DatasetItem.group_hash,
        )
        .outerjoin(DatasetFamily, DatasetItem.lineage_group_id == DatasetFamily.id)
        .where(DatasetItem.dataset_id == dataset_id)
        .limit(10001)
    ).all()
    if len(items) > 10000:
        raise LineageViolation("LINEAGE_DATASET_EXCEEDS_BOUND")
    selected = {
        canonical for _, split, canonical, _ in items if split == mode and canonical is not None
    }
    complete = bool(selected) and all(canonical is not None for _, _, canonical, _ in items)
    group_hashes: dict[UUID, set[str]] = {}
    for _, _, canonical, group_hash in items:
        if canonical in selected:
            group_hashes.setdefault(canonical, set()).add(group_hash)
    if any(len(values) > 1 for values in group_hashes.values()):
        raise LineageViolation("DATASET_GLOBAL_GROUPING_CHANGED_REIMPORT_REQUIRED")
    if mode == "test" and selected & {
        canonical for _, split, canonical, _ in items if split != "test"
    }:
        raise LineageViolation("DATASET_GLOBAL_FAMILY_SPANS_TEST_AND_TRAIN_OR_VALIDATION")
    return selected, complete


def legacy_test_history(session, project):
    return bool(
        session.scalar(
            select(
                exists(
                    select(DatasetItem.id)
                    .join(DatasetRecord, DatasetItem.dataset_id == DatasetRecord.id)
                    .join(ExperimentPlanRecord, ExperimentPlanRecord.dataset_id == DatasetRecord.id)
                    .join(
                        ExperimentRunRecord, ExperimentRunRecord.plan_id == ExperimentPlanRecord.id
                    )
                    .where(
                        DatasetRecord.project_id == project,
                        DatasetItem.lineage_group_id.is_(None),
                        ExperimentPlanRecord.specification["public_specification"]["operation"]
                        .as_string()
                        .is_distinct_from("candidate-generation-v1"),
                        ExperimentPlanRecord.specification["public_specification"]["split"]
                        .as_string()
                        .is_distinct_from("validation"),
                    )
                )
            )
        )
    )


def inspect_exposure(session, project, dataset_id, mode, *, source_ref=None, require_fresh=False):
    lock_lineage(session, project)
    families, complete = _families(session, project, dataset_id, mode)
    query = (
        select(DatasetExposure)
        .join(DatasetFamily, DatasetExposure.family_id == DatasetFamily.id)
        .where(DatasetExposure.project_id == project, DatasetFamily.canonical_id.in_(families))
    )
    if source_ref:
        query = query.where(DatasetExposure.source_ref != source_ref)
    previous = set(
        session.scalars(query.with_only_columns(DatasetFamily.canonical_id).distinct().limit(10001))
    )
    if len(previous) > 10000:
        raise LineageViolation("LINEAGE_HISTORY_EXCEEDS_BOUND")
    active = session.scalar(
        query.join(ExperimentRunRecord, DatasetExposure.run_id == ExperimentRunRecord.id)
        .where(
            DatasetExposure.protected.is_(True),
            ExperimentRunRecord.status.in_(["QUEUED", "RUNNING"]),
        )
        .limit(1)
    )
    if active:
        raise LineageViolation("INDEPENDENT_TEST_GROUPS_ALREADY_RESERVED")
    if require_fresh:
        if not complete or legacy_test_history(session, project):
            raise LineageViolation("LEGACY_OR_INCOMPLETE_LINEAGE_RECONCILIATION_REQUIRED")
        if previous:
            raise LineageViolation("TEST_FAMILIES_PREVIOUSLY_EXPOSED_IN_THIS_PROJECT")
    return families, {
        "contract": "cutback.lineage.v1",
        "complete": complete,
        "groups": len(families),
        "previously_exposed_groups": len(previous),
        "fresh_test_claim": mode == "test"
        and complete
        and not previous
        and not legacy_test_history(session, project),
    }


def expose(session, project, dataset_id, mode, source_ref, *, run_id=None, protected=False):
    if (
        mode not in {"train", "validation", "test"}
        or protected
        and (mode != "test" or run_id is None)
    ):
        raise LineageViolation("LINEAGE_RESERVATION_SCOPE_INVALID")
    families, receipt = inspect_exposure(
        session, project, dataset_id, mode, source_ref=source_ref, require_fresh=protected
    )
    for family in sorted(families, key=str):
        session.execute(
            insert(DatasetExposure)
            .values(
                id=uuid4(),
                project_id=project,
                family_id=family,
                dataset_id=dataset_id,
                run_id=run_id,
                mode=mode,
                source_ref=source_ref,
                protected=protected,
            )
            .on_conflict_do_nothing(constraint="uq_dataset_exposure")
        )
    return receipt


def independent_test(plan):
    return plan.split == "test" and (
        bool(plan.predecessor_run_id) or plan.staged and plan.stage == "full"
    )


def reserve_evaluation(session, project, dataset_id, plan, run_id):
    source = "evaluation:" + str(run_id)
    receipt = expose(
        session,
        project,
        dataset_id,
        plan.split,
        source,
        run_id=run_id,
        protected=independent_test(plan),
    )
    if plan.pilot_samples:
        expose(session, project, dataset_id, "validation", source + ":pilot", run_id=run_id)
    return receipt


def evaluation_receipt(session, project, dataset_id, plan, run_id):
    source = "evaluation:" + str(run_id)
    _, receipt = inspect_exposure(
        session,
        project,
        dataset_id,
        plan.split,
        source_ref=source,
        require_fresh=independent_test(plan),
    )
    own = session.scalar(
        select(DatasetExposure.id)
        .where(DatasetExposure.project_id == project, DatasetExposure.source_ref == source)
        .limit(1)
    )
    if not own:
        receipt = {
            **receipt,
            "complete": False,
            "fresh_test_claim": False,
            "reservation_missing": True,
        }
        if independent_test(plan):
            raise LineageViolation("INDEPENDENT_TEST_LINEAGE_RESERVATION_REQUIRED")
    return receipt


def require_current_test_evidence(session, store, recommendation):
    """Revalidate family discoveries since execution before authorizing a source change."""
    from cutback.server.plan_artifacts import load_plan

    project = recommendation.project_id
    lock_lineage(session, project)
    run = session.get(ExperimentRunRecord, recommendation.run_id)
    row = session.get(ExperimentPlanRecord, run.plan_id) if run else None
    if not row or row.project_id != project or run.status != "COMPLETED":
        raise LineageViolation("COMPLETED_TEST_EVIDENCE_REQUIRED")
    plan = load_plan(store, project, row.specification)
    from cutback.evals.input_groups import effective_input_groups, requires_projection

    if requires_projection(plan):
        from cutback.server.worker import load_dataset

        dataset_row = session.get(DatasetRecord, row.dataset_id)
        try:
            loaded, _ = load_dataset(session, dataset_row, store)
            _, grouping = effective_input_groups(plan, loaded)
        except ValueError:
            raise LineageViolation("NATIVE_INPUT_GROUPING_RECONFIRMATION_REQUIRED") from None
        if recommendation.evidence.get("input_grouping") != grouping:
            raise LineageViolation("NATIVE_INPUT_GROUPING_RECONFIRMATION_REQUIRED")
    if (
        plan.split != "test"
        or recommendation.evidence.get("lineage", {}).get("fresh_test_claim") is not True
    ):
        raise LineageViolation("TEST_LINEAGE_REAPPROVAL_REQUIRED")
    receipt = evaluation_receipt(session, project, row.dataset_id, plan, run.id)
    if receipt.get("fresh_test_claim") is not True:
        raise LineageViolation("TEST_LINEAGE_NO_LONGER_INDEPENDENT")
    return receipt


def apply_lineage_result(results, receipt, split):
    results["lineage"] = receipt
    for recommendation in results.get("recommendations", []):
        recommendation["lineage"] = receipt
        if split == "test" and not receipt.get("fresh_test_claim"):
            recommendation["sample_quality_gate_passed"] = recommendation.get("quality_gate_passed")
            recommendation.update(
                quality_gate_passed=False,
                status="NEEDS_NEW_CONFIRMATION",
                decision_reason="test_group_lineage_not_fresh",
            )
