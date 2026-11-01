"""Authenticate train-only routing evidence against scoped native call receipts."""

import json
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select

from cutback.core.input_routing import LearnedRoutingBinding, RoutingTrainingExecution
from cutback.evals.datasets import digest
from cutback.evals.learned_routing import (
    RoutingTrainingEvidence,
    SourcePolicyRoutingTrainingEvidence,
    fit_routes,
)
from cutback.evals.metrics import evaluate
from cutback.server.content_policy import policy_content, require_payloads_enabled
from cutback.server.dataset_lineage import inspect_exposure
from cutback.server.db import (
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import money
from cutback.server.lineage_db import DatasetFamily
from cutback.server.plan_artifacts import load_plan
from cutback.server.principals import principal_role


def fit_training(
    session, store, project, training_run_id, options, *, identifier, request_hash, actor, refs
):
    """Persist a nonbillable CPU fit using the same authenticated native receipts.

    The caller owns the project lock/content transaction and idempotency lookup.
    No provider request occurs; compute and hosting dollars remain unknown.
    """
    from cutback.server.evaluation_api import policy_object_count

    started = time.perf_counter()
    evidence, training, dataset, policy_id, policy, expiry = authenticated_training(
        session, store, project, training_run_id
    )
    if training.routing_execution.source:
        from cutback.evals.routing_source import fit_source_routes

        result = fit_source_routes(evidence, options, training.routing_execution.source)
    else:
        result = fit_routes(evidence, options)
    value = {
        "version": "cutback.learned-routing-artifact.v1",
        "content_nonce": str(uuid4()),
        "training_run_id": str(training_run_id),
        "dataset_id": str(dataset.id),
        "training_plan_sha256": evidence.source_plan_sha256,
        "training_dataset_sha256": evidence.dataset_sha256,
        "fit": result,
        "fit_elapsed_ms": (time.perf_counter() - started) * 1000,
        "fit_compute_usd": None,
        "fit_provider_requests": 0,
        "training_setup_known_cost_usd": str(
            sum(
                (
                    row.known_cost_usd
                    for row in evidence.observations
                    if row.known_cost_usd is not None
                ),
                start=Decimal(0),
            )
        ),
        "training_evidence": evidence.model_dump(mode="json"),
    }
    policy_row = session.get(
        CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True
    )
    if (
        not policy_row
        or not policy_row.enabled
        or policy_row.policy != policy.model_dump(mode="json")
        or policy.expires_at <= datetime.now(UTC)
        or policy_object_count(session, policy_id) >= policy.max_samples
    ):
        raise ValueError("ROUTING_SHARED_CONTENT_QUOTA_EXHAUSTED")
    clean, blob = policy_content(value, policy, preserve_semantics=True)
    ref = store.put(project, blob, policy.retention_days)
    refs.append(ref)
    sample = PayloadSample(
        id=identifier,
        project_id=project,
        policy_id=policy_id,
        object_ref=ref,
        content_hash=digest(clean),
        expires_at=min(expiry, datetime.now(UTC) + timedelta(days=policy.retention_days)),
        metadata_safe={
            "kind": "learned_routing_fit",
            "request_hash": request_hash,
            "created_by": actor,
            "training_run_id": str(training_run_id),
            "dataset_id": str(dataset.id),
            "training_cases": result["training_cases"],
            "training_groups": result["training_groups"],
        },
    )
    session.add(sample)
    session.flush()
    return sample


def reserve_training(session, run, environment_id, plan_sha256):
    """The ordinary run ledger owns this separate training budget, never a proposer loophole."""
    from cutback.server.db import Environment

    planned = session.get(ExperimentPlanRecord, run.plan_id, populate_existing=True)
    environment = session.get(Environment, environment_id, populate_existing=True)
    dataset = session.get(DatasetRecord, planned.dataset_id) if planned else None
    public = planned.specification.get("public_specification", {}) if planned else {}
    if (
        not planned
        or planned.project_id != run.project_id
        or planned.approval_status != "consumed"
        or planned.specification.get("plan_hash") != plan_sha256
        or public.get("evaluator_version")
        != (
            "deterministic-v16"
            if public.get("routing_execution", {}).get("source")
            else "deterministic-v15"
        )
        or public.get("split") != "train"
        or public.get("routing_execution", {}).get("phase") != "training"
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
        raise ValueError("ROUTING_TRAINING_EXACT_APPROVED_SCOPE_REQUIRED")


def check_training_scope(session, project, dataset, maximum):
    families, receipt = inspect_exposure(session, project, dataset.id, "train")
    if receipt["complete"] is not True:
        raise ValueError("ROUTING_REQUIRES_COMPLETE_TRAINING_LINEAGE")
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
        raise ValueError("ROUTING_TRAINING_FAMILY_OVERLAPS_KNOWN_TEST_ASSIGNMENT")
    items = session.scalars(
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset.id, DatasetItem.split == "train")
        .order_by(DatasetItem.id)
        .limit(maximum + 1)
    ).all()
    if not 2 <= len(items) <= maximum:
        raise ValueError("ROUTING_TRAINING_CASE_BOUND")
    return items


def _payload(session, store, project, policy_id, identifier, policy, *, remaining_bytes=None):
    row = session.get(PayloadSample, identifier, populate_existing=True)
    if (
        not row
        or row.project_id != project
        or row.policy_id != policy_id
        or row.expires_at <= datetime.now(UTC)
        or row.metadata_safe.get("deleted")
    ):
        raise ValueError("ROUTING_PRIVATE_EVIDENCE_EXPIRED_OR_OUT_OF_SCOPE")
    try:
        raw = store.get(project, row.object_ref)
    except Exception:
        raise ValueError("ROUTING_PRIVATE_EVIDENCE_UNAVAILABLE_OR_CHANGED") from None
    if remaining_bytes is not None:
        remaining_bytes[0] -= len(raw)
        if remaining_bytes[0] < 0:
            raise ValueError("ROUTING_PRIVATE_EVIDENCE_READ_BOUND")
    try:
        if len(raw) > min(policy.max_payload_bytes, 512000):
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or digest(value) != row.content_hash:
            raise ValueError
    except Exception:
        raise ValueError("ROUTING_PRIVATE_EVIDENCE_UNAVAILABLE_OR_CHANGED") from None
    policy_content(value, policy, preserve_semantics=True)
    return row, value


def training_source(session, store, project, run_id, *, source_policy=False):
    from cutback.server.evaluation_api import active_policy

    require_payloads_enabled()
    run = session.get(ExperimentRunRecord, run_id, populate_existing=True)
    if (
        not run
        or run.project_id != project
        or run.status != "COMPLETED"
        or run.results.get("billing_complete") is not True
        or run.call_ledger_version != 1
    ):
        raise ValueError("ROUTING_REQUIRES_COMPLETED_CANONICAL_TRAINING_RUN")
    planned = session.get(ExperimentPlanRecord, run.plan_id, populate_existing=True)
    if (
        not planned
        or planned.project_id != project
        or planned.approval_status != "consumed"
        or principal_role(session, project, planned.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("ROUTING_TRAINING_APPROVAL_OR_PRINCIPAL_NO_LONGER_AUTHORIZED")
    plan = load_plan(store, project, planned.specification)
    execution = plan.source_policy_execution if source_policy else plan.routing_execution
    if source_policy:
        if execution is None or execution.phase != "training" or plan.split != "train":
            raise ValueError("POLICY_REQUIRES_EXPLICIT_TRAIN_ONLY_SOURCE")
        from cutback.server.policy_service import validate_binding

        validate_binding(session, store, project, plan, planned.dataset_id)
    elif not isinstance(execution, RoutingTrainingExecution) or plan.split != "train":
        raise ValueError("ROUTING_REQUIRES_EXPLICIT_TRAIN_ONLY_SOURCE")
    dataset = session.get(DatasetRecord, planned.dataset_id, populate_existing=True)
    if (
        not dataset
        or dataset.project_id != project
        or dataset.immutable_hash != plan.dataset_hash
        or dataset.workflow_id != plan.workflow
    ):
        raise ValueError("ROUTING_TRAINING_DATASET_CHANGED_OR_OUT_OF_SCOPE")
    policy_id = UUID(dataset.provenance["policy_id"])
    _, policy = active_policy(session, project, policy_id)
    if not policy.permits(dataset.provenance["environment"], dataset.workflow_id) or any(
        provider not in policy.allowed_providers for provider in plan.approved_providers
    ):
        raise ValueError("ROUTING_TRAINING_CONTENT_RIGHTS_REVOKED")
    items = check_training_scope(session, project, dataset, min(execution.max_cases, 256))
    return run, planned, plan, dataset, policy_id, policy, items


def authenticated_training(session, store, project, run_id, *, source_policy=False):
    run, planned, plan, dataset, policy_id, policy, items = training_source(
        session, store, project, run_id, source_policy=source_policy
    )
    cases = {}
    remaining_bytes = [16 * 1024 * 1024]
    expiry = min(datetime.fromisoformat(planned.specification["expires_at"]), policy.expires_at)
    for item in items:
        sample, case = _payload(
            session,
            store,
            project,
            policy_id,
            item.payload_id,
            policy,
            remaining_bytes=remaining_bytes,
        )
        if case.get("gold") is None or case.get("episode") or case.get("security"):
            raise ValueError("ROUTING_REQUIRES_LABELED_NATIVE_TEXT_TRAINING_CASES")
        if not isinstance(case.get("input"), str) or not isinstance(case.get("id"), str):
            raise ValueError("ROUTING_TRAINING_CASE_FORMAT_INVALID")
        if case["id"] in cases:
            raise ValueError("ROUTING_TRAINING_CASE_ID_CONFLICT")
        cases[case["id"]] = (case, item.group_hash)
        expiry = min(expiry, sample.expires_at)
    models = [plan.baseline, *plan.candidates]
    by_key = {model.key: model for model in models}
    canonical_models = by_key
    if source_policy:
        from cutback.evals.policy_artifacts import decode_policy_bundle
        from cutback.evals.policy_models import policy_leaf_models, resolve_policy_target
        from cutback.server.policy_service import validate_binding

        payloads = validate_binding(session, store, project, plan, dataset.id)
        policies = decode_policy_bundle(payloads, plan.source_policy_execution)
        leaves = policy_leaf_models(plan.source_policy_execution, policies)
        if any(target.exact_cache is not None for values in leaves.values() for target in values):
            raise ValueError("POLICY_TRAINING_REQUIRES_UNCACHED_NATIVE_LEAVES")
    expected = len(cases) * len(models)
    payloads = session.scalars(
        select(PayloadSample)
        .where(
            PayloadSample.project_id == project,
            PayloadSample.metadata_safe["kind"].as_string() == "experiment_case",
            PayloadSample.metadata_safe["run_id"].as_string() == str(run.id),
        )
        .order_by(PayloadSample.id)
        .limit(expected + 1)
    ).all()
    calls = session.scalars(
        select(ExecutionCall)
        .where(ExecutionCall.project_id == project, ExecutionCall.run_id == run.id)
        .limit(expected + 1)
    ).all()
    if len(payloads) != expected or len(calls) != expected or run.requests_used != expected:
        raise ValueError("ROUTING_TRAINING_PAIRED_RECEIPTS_INCOMPLETE")
    call_rows = {str(call.id): call for call in calls}
    retained = {}
    seen_calls = set()
    for payload in payloads:
        if payload.metadata_safe.get("semantics_preserved") is not True:
            raise ValueError("ROUTING_TRAINING_RETAINED_SEMANTICS_CHANGED")
        sample, value = _payload(
            session, store, project, policy_id, payload.id, policy, remaining_bytes=remaining_bytes
        )
        expiry = min(expiry, sample.expires_at)
        case = value.get("case", {})
        identity = (case.get("id"), value.get("model_key"))
        if (
            identity in retained
            or identity[0] not in cases
            or identity[1] not in by_key
            or digest(case) != digest(cases[identity[0]][0])
            or value.get("repeat") != 0
            or value.get("complete") is not True
            or not isinstance(value.get("output"), str)
        ):
            raise ValueError("ROUTING_TRAINING_RETAINED_CASE_CHANGED_OR_INCOMPLETE")
        receipt = value.get("native_call")
        if not isinstance(receipt, dict):
            raise ValueError("ROUTING_TRAINING_CANONICAL_CASE_RECEIPT_REQUIRED")
        call = call_rows.get(str(receipt.get("id")))
        model = (
            resolve_policy_target(
                plan.source_policy_execution, policies, by_key[identity[1]], case["input"]
            )
            if source_policy
            else canonical_models[identity[1]]
        )
        if (
            not call
            or call.id in seen_calls
            or call.state != "completed"
            or call.complete is not True
            or call.spent_usd is None
            or call.model_key != model.key
            or call.provider != model.provider
            or call.model != model.model
            or call.purpose != "optimization"
            or call.price_sha256 != plan.price_sha256
            or receipt.get("price_sha256") != plan.price_sha256
            or source_policy
            and call.cache_observation is not None
        ):
            raise ValueError("ROUTING_TRAINING_CALL_RECEIPT_CHANGED_OR_UNCERTAIN")
        try:
            observed_cost = Decimal(receipt["known_cost_usd"])
            if (
                not observed_cost.is_finite()
                or observed_cost < 0
                or money(observed_cost) != call.spent_usd
            ):
                raise ValueError
        except Exception:
            raise ValueError("ROUTING_TRAINING_COST_RECEIPT_MISMATCH") from None
        seen_calls.add(call.id)
        retained[identity] = (value["output"], call.spent_usd)
    metrics = [
        plan.metric,
        *[criterion.metric for criterion in plan.criteria if criterion.required],
    ]
    observations = []
    for case_id, (case, group) in sorted(cases.items()):
        baseline_output = retained.get((case_id, plan.baseline.key))
        if baseline_output is None:
            raise ValueError("ROUTING_TRAINING_BASELINE_CASE_MISSING")
        baseline_scores = [
            evaluate([case], [baseline_output[0]], metric, independent_labels=False)["value"]
            for metric in metrics
        ]
        for model in models:
            result = retained.get((case_id, model.key))
            if result is None:
                raise ValueError("ROUTING_TRAINING_PAIRED_CASE_MISSING")
            scores = [
                evaluate([case], [result[0]], metric, independent_labels=False)["value"]
                for metric in metrics
            ]
            acceptable = all(
                score is not None
                and baseline is not None
                and score >= metric.minimum
                and score >= baseline - metric.max_regression
                for metric, baseline, score in zip(metrics, baseline_scores, scores, strict=True)
            )
            observations.append(
                {
                    "case_sha256": digest(case_id),
                    "group_sha256": group,
                    "model_key": model.key,
                    "input_bytes": len(case["input"].encode()),
                    "acceptable": acceptable,
                    "known_cost_usd": result[1],
                }
            )
    evidence_type = (
        SourcePolicyRoutingTrainingEvidence
        if source_policy and plan.baseline.routes
        else RoutingTrainingEvidence
    )
    evidence = evidence_type.model_validate(
        dict(
            split="train",
            dataset_sha256=dataset.immutable_hash,
            source_plan_sha256=planned.specification["plan_hash"],
            source_run_id=run.id,
            baseline_key=plan.baseline.key,
            models=models,
            observations=observations,
        )
    )
    return evidence, plan, dataset, policy_id, policy, expiry


def read_fit(session, store, project, fit_id):
    from cutback.server.evaluation_api import active_policy

    require_payloads_enabled()
    sample = session.get(PayloadSample, fit_id, populate_existing=True)
    if (
        not sample
        or sample.project_id != project
        or sample.metadata_safe.get("kind") != "learned_routing_fit"
    ):
        raise ValueError("ROUTING_FIT_NOT_FOUND_OR_OUT_OF_SCOPE")
    _, policy = active_policy(session, project, sample.policy_id)
    sample, value = _payload(session, store, project, sample.policy_id, sample.id, policy)
    context = training_source(session, store, project, UUID(value["training_run_id"]))
    _, planned, training, dataset, policy_id, _, _ = context
    if (
        sample.policy_id != policy_id
        or value["dataset_id"] != str(dataset.id)
        or value["training_plan_sha256"] != planned.specification["plan_hash"]
        or value["training_dataset_sha256"] != dataset.immutable_hash
    ):
        raise ValueError("ROUTING_FIT_SOURCE_BINDING_CHANGED")
    return sample, value, training, dataset


def validate_binding(session, store, project, plan, *, dataset_id=None):
    if plan.routing_execution is None:
        return None
    if isinstance(plan.routing_execution, RoutingTrainingExecution):
        if dataset_id is None:
            raise ValueError("ROUTING_TRAINING_DATASET_ID_REQUIRED")
        dataset = session.get(DatasetRecord, dataset_id, populate_existing=True)
        if (
            not dataset
            or dataset.project_id != project
            or dataset.immutable_hash != plan.dataset_hash
        ):
            raise ValueError("ROUTING_TRAINING_DATASET_SCOPE_MISMATCH")
        check_training_scope(session, project, dataset, plan.routing_execution.max_cases)
        return None
    binding = LearnedRoutingBinding.model_validate(plan.routing_execution)
    sample, value, training, dataset = read_fit(session, store, project, binding.fit_id)
    if (
        sample.content_hash != binding.fit_sha256
        or value["training_run_id"] != str(binding.training_run_id)
        or plan.dataset_hash != dataset.immutable_hash
        or (dataset_id is not None and dataset_id != dataset.id)
        or plan.workflow != training.workflow
        or plan.baseline != training.baseline
        or plan.metric != training.metric
        or plan.criteria != training.criteria
        or plan.approved_providers != training.approved_providers
        or binding.transport != training.routing_execution.transport
        or binding.source != training.routing_execution.source
        or plan.candidates[0].model_dump(mode="json") != value["fit"]["candidate"]
    ):
        raise ValueError("ROUTING_FIT_FROZEN_POLICY_OR_OWNER_REQUIREMENTS_CHANGED")
    return sample, value


def receipt(session, store, project, plan):
    if not isinstance(plan.routing_execution, LearnedRoutingBinding):
        return {}
    from fastapi import HTTPException

    try:
        _, value, _, _ = read_fit(session, store, project, plan.routing_execution.fit_id)
    except (ValueError, HTTPException):
        return {
            "routing_fit_binding": plan.routing_execution.model_dump(mode="json"),
            "routing_source_current": False,
            "routing_setup_cost": {
                "shared_setup_key": str(plan.routing_execution.training_run_id),
                "training_known_cost_usd": None,
                "fit_compute_usd": None,
                "routing_hosting_cost_usd": None,
                "amortized_per_task_usd": None,
                "included_in_target_evaluation_spend": False,
            },
            "routing_source_patch_eligible": False,
        }
    return {
        "routing_fit_binding": plan.routing_execution.model_dump(mode="json"),
        "routing_source_current": True,
        "routing_setup_cost": {
            "shared_setup_key": value["training_run_id"],
            "training_known_cost_usd": value["training_setup_known_cost_usd"],
            "fit_compute_usd": None,
            "routing_hosting_cost_usd": None,
            "amortized_per_task_usd": None,
            "included_in_target_evaluation_spend": False,
        },
        "routing_source_patch_eligible": False,
    }
