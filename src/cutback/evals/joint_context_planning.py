"""A finite model/context comparison. No inference, imports of source or billable work."""

from uuid import uuid4

from cutback.core.compression import CompressionSettings, CompressionVariant
from cutback.evals.compression import JointModelContextExecutionSpec
from cutback.evals.compression_planning import candidate_settings
from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan, qualified_native_model
from cutback.evals.search import candidate_portfolio


def joint_candidates(baseline, settings, *, max_candidates=5, models=None, contexts=None):
    if type(max_candidates) is not int or not 1 <= max_candidates <= 5:
        raise ValueError("JOINT_SEARCH_LIMIT_ONE_TO_FIVE_REQUIRED")
    qualified_native_model(baseline)
    if models is None:
        try:
            proposed = candidate_portfolio(baseline, limit=3)
        except ValueError as exc:
            if str(exc) != "NO_QUALIFIED_SEARCH_VARIANT_SUBMIT_REVIEWED_CANDIDATE":
                raise
            proposed = []
        alternatives = []
        for model in proposed:
            try:
                qualified_native_model(model)
            except ValueError:
                continue
            alternatives.append(model)
    else:
        alternatives = list(models)
        if len(alternatives) > 5:
            raise ValueError("JOINT_NATIVE_MODEL_CANDIDATE_BOUND")
        for model in alternatives:
            qualified_native_model(model)
    selections = (
        tuple(CompressionSettings.model_validate(value) for value in contexts)
        if contexts is not None
        else candidate_settings(settings)
    )
    if not 1 <= len(selections) <= 5 or any(
        value.max_context_bytes > settings.max_context_bytes for value in selections
    ):
        raise ValueError("JOINT_CONTEXT_CANDIDATES_CANNOT_WIDEN_INPUT_BOUND")
    pairs = []
    if alternatives:
        pairs.append((alternatives[0], settings))
    for value in selections:
        pairs.append((baseline, value))
        if alternatives:
            pairs.append((alternatives[0], value))
    for model in alternatives[1:]:
        pairs.extend([(model, settings), (model, selections[0])])
    seen = {
        digest(
            [baseline.model_dump(mode="json", exclude={"key"}), settings.model_dump(mode="json")]
        )
    }
    result = []
    for model, value in pairs:
        identity = digest(
            [model.model_dump(mode="json", exclude={"key"}), value.model_dump(mode="json")]
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append((model.model_copy(update={"key": "joint-" + identity[:12]}), value))
        if len(result) == max_candidates:
            break
    if not result:
        raise ValueError("NO_DISTINCT_JOINT_MODEL_CONTEXT_CANDIDATE")
    return tuple(result)


def draft_joint_plan(
    dataset,
    baseline,
    settings,
    *,
    metric,
    budget,
    models=None,
    contexts=None,
    max_candidates=5,
    assumed_usd_per_cpu_hour=None,
    max_query_bytes=1024,
    retain_case_outputs=False,
    configuration_binding=None,
    producer_binding=None,
    transport="native_provider",
):
    if not dataset["provenance"].get("independent_labels"):
        raise ValueError("JOINT_SEARCH_REQUIRES_INDEPENDENT_TASK_LABELS")
    pairs = joint_candidates(
        baseline, settings, max_candidates=max_candidates, models=models, contexts=contexts
    )
    models = [baseline, *[model for model, _ in pairs]]
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=models[1:],
        metric=metric,
        budget=budget,
        approved_providers=[baseline.provider],
        split="validation",
        evaluator_version="deterministic-v12",
        content_nonce=uuid4(),
        retain_case_outputs=retain_case_outputs,
        compression_execution=JointModelContextExecutionSpec(
            transport=transport,
            variants=tuple(
                CompressionVariant(model_key=model.key, settings=value)
                for model, value in [(baseline, settings), *pairs]
            ),
            max_query_bytes=max_query_bytes,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            configuration_binding=configuration_binding,
            producer_binding=producer_binding,
        ),
    )


def confirmation_plan(prior, report, run_id, budget):
    if (
        not isinstance(prior.compression_execution, JointModelContextExecutionSpec)
        or prior.split != "validation"
        or prior.predecessor_run_id
        or report.get("status") != "COMPLETED"
        or report.get("billing_complete") is not True
    ):
        raise ValueError("COMPLETED_JOINT_VALIDATION_REQUIRED")
    selected = next(
        (
            row["candidate"]["key"]
            for row in report.get("recommendations", [])
            if row.get("joint_model_context_confirmation_eligible") is True
        ),
        None,
    )
    model = next((model for model in prior.candidates if model.key == selected), None)
    if model is None:
        raise ValueError("NO_JOINT_CANDIDATE_PASSED_COMPONENT_COST_AND_QUALITY")
    raw = prior.model_dump(mode="json")
    raw.update(
        split="test",
        sample_limit=None,
        predecessor_run_id=run_id,
        candidates=[model.model_dump(mode="json")],
        budget=budget.model_dump(mode="json"),
    )
    raw["compression_execution"]["variants"] = [
        value.model_dump(mode="json")
        for value in prior.compression_execution.variants
        if value.model_key in {prior.baseline.key, selected}
    ]
    return ExperimentPlan.model_validate(raw)


def regression_gate(report, *, expected_plan_hash, candidate=None, sample_only=False):
    """Exact v12 accounting, component economics, quality and confirmation gate."""
    reasons = []
    if report.get("synthetic_execution") and not sample_only:
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    if report.get("status") != "COMPLETED" or report.get("billing_complete") is not True:
        reasons.append("run_or_accounting_incomplete")
    if report.get("plan", {}).get("plan_hash") != expected_plan_hash:
        reasons.append("frozen_plan_mismatch")
    if report.get("plan", {}).get("split") != "test":
        reasons.append("independent_test_confirmation_required")
    if report.get("joint_model_context", {}).get("version") != "cutback.joint-model-context.v1":
        reasons.append("joint_execution_contract_required")
    if report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    choices = [
        row
        for row in report.get("recommendations", [])
        if (candidate is None or row["candidate"]["key"] == candidate)
        and row.get("evaluator_version") == "deterministic-v12"
        and row.get("joint_model_context_confirmation_eligible") is True
        and row.get("quality_gate_passed") is True
        and row.get("cheaper_on_sample") is True
    ]
    if not sample_only:
        choices = [
            row
            for row in choices
            if row.get("evidence_tier") == 4
            and row.get("statistical_gate_passed") is True
            and row.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        ]
    if not choices:
        reasons.append("no_joint_candidate_meets_component_cost_and_quality_gates")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": sorted(row["candidate"]["key"] for row in choices) if not reasons else [],
        "plan_hash": expected_plan_hash,
        "production_deployed": False,
        "production_savings_usd": None,
    }
