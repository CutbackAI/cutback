"""Finite, data-independent model/output/retrieval hypotheses and frozen confirmation."""

from uuid import uuid4

from cutback.core.retrieval import RetrievalSettings, RetrievalVariant
from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan, ModelSpec, qualified_native_model
from cutback.evals.retrieval_joint import JointModelRetrievalExecutionSpec
from cutback.evals.retrieval_planning import candidate_settings
from cutback.evals.search import candidate_portfolio
from cutback.pricing import Catalog


def candidate_pairs(baseline: ModelSpec, settings: RetrievalSettings, *, limit=5):
    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("JOINT_RETRIEVAL_REQUIRES_ONE_TO_FIVE_CANDIDATES")
    if (
        baseline.provider not in {"openai", "anthropic"}
        or baseline.routes
        or baseline.prompt_cache
        or baseline.exact_cache
        or baseline.agent_turn
    ):
        raise ValueError("JOINT_RETRIEVAL_REQUIRES_QUALIFIED_UNCACHED_NATIVE_TEXT")
    qualified_native_model(baseline, Catalog())
    native = []
    try:
        native = candidate_portfolio(baseline, limit=2)
    except ValueError as exc:
        if str(exc) != "NO_QUALIFIED_SEARCH_VARIANT_SUBMIT_REVIEWED_CANDIDATE":
            raise
    retrieval = candidate_settings(settings)
    raw = [(model, settings) for model in native]
    raw += [(baseline, value) for value in retrieval[:2]]
    raw += [(model, value) for model in native for value in retrieval]
    raw += [(baseline, value) for value in retrieval[2:]]
    pairs: list[tuple[ModelSpec, RetrievalSettings]] = []
    seen: set[tuple[str, str]] = set()
    for model, value in raw:
        semantic = (model.model_dump_json(exclude={"key"}), value.model_dump_json())
        if semantic in seen:
            continue
        seen.add(semantic)
        key = next(
            f"joint-retrieval-{index}"
            for index in range(1, 8)
            if f"joint-retrieval-{index}" not in {baseline.key, *(item[0].key for item in pairs)}
        )
        pairs.append((model.model_copy(update={"key": key}), value))
        if len(pairs) == limit:
            break
    if not pairs:
        raise ValueError("NO_DISTINCT_JOINT_RETRIEVAL_CANDIDATE")
    return pairs


def draft_plan(
    dataset,
    baseline,
    corpus,
    settings,
    *,
    metric,
    budget,
    max_candidates=5,
    assumed_usd_per_cpu_hour=None,
    max_query_bytes=1024,
    retain_case_outputs=False,
    configuration_binding=None,
    producer_binding=None,
):
    provenance = dataset["provenance"]
    if not provenance.get("independent_labels"):
        raise ValueError("JOINT_RETRIEVAL_REQUIRES_INDEPENDENT_ANSWER_LABELS")
    if provenance.get("max_input_bytes", 0) > max_query_bytes:
        raise ValueError("RETRIEVAL_DATASET_QUERY_EXCEEDS_APPROVED_BOUND")
    pairs = candidate_pairs(baseline, settings, limit=max_candidates)
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=[model for model, _ in pairs],
        metric=metric,
        budget=budget,
        approved_providers=[baseline.provider],
        split="validation",
        evaluator_version="deterministic-v18",
        content_nonce=uuid4(),
        retain_case_outputs=retain_case_outputs,
        retrieval_execution=JointModelRetrievalExecutionSpec(
            corpus=corpus,
            configuration_binding=configuration_binding,
            producer_binding=producer_binding,
            max_query_bytes=max_query_bytes,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            variants=tuple(
                RetrievalVariant(model_key=model.key, settings=value)
                for model, value in [(baseline, settings), *pairs]
            ),
        ),
    )


def confirmation_plan(prior, report, run_id, budget, *, candidate=None):
    if (
        not isinstance(prior.retrieval_execution, JointModelRetrievalExecutionSpec)
        or prior.evaluator_version != "deterministic-v18"
        or prior.split != "validation"
        or prior.predecessor_run_id
        or report.get("status") != "COMPLETED"
        or report.get("billing_complete") is not True
        or report.get("plan", {}).get("plan_hash") != digest(prior.model_dump(mode="json"))
    ):
        raise ValueError("COMPLETED_JOINT_RETRIEVAL_VALIDATION_REQUIRED")
    selected = next(
        (
            row["candidate"]["key"]
            for row in report.get("recommendations", [])
            if row.get("joint_model_retrieval_confirmation_eligible") is True
            and (candidate is None or row["candidate"]["key"] == candidate)
        ),
        None,
    )
    model = next((item for item in prior.candidates if item.key == selected), None)
    if model is None:
        raise ValueError("NO_JOINT_RETRIEVAL_CANDIDATE_PASSED_COMPONENT_COST_AND_QUALITY")
    raw = prior.model_dump(mode="json")
    raw.update(
        split="test",
        sample_limit=None,
        predecessor_run_id=run_id,
        candidates=[model.model_dump(mode="json")],
        budget=budget.model_dump(mode="json"),
    )
    raw["retrieval_execution"]["variants"] = [
        value.model_dump(mode="json")
        for value in prior.retrieval_execution.variants
        if value.model_key in {prior.baseline.key, selected}
    ]
    return ExperimentPlan.model_validate(raw)


def regression_gate(report, *, expected_plan_hash, candidate=None, sample_only=False):
    reasons = []
    if report.get("status") != "COMPLETED" or report.get("billing_complete") is not True:
        reasons.append("run_or_accounting_incomplete")
    if report.get("plan", {}).get("plan_hash") != expected_plan_hash:
        reasons.append("frozen_plan_mismatch")
    if report.get("plan", {}).get("split") != "test":
        reasons.append("independent_test_confirmation_required")
    if report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    if report.get("joint_model_retrieval", {}).get("version") != "cutback.joint-model-retrieval.v1":
        reasons.append("joint_retrieval_execution_contract_required")
    if (report.get("native_fixture") or report.get("synthetic_execution")) and not sample_only:
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    rows = [
        row
        for row in report.get("recommendations", [])
        if (candidate is None or row["candidate"]["key"] == candidate)
        and row.get("evaluator_version") == "deterministic-v18"
        and row.get("joint_model_retrieval_confirmation_eligible") is True
        and row.get("quality_gate_passed") is True
        and row.get("cheaper_on_sample") is True
    ]
    if not sample_only:
        rows = [
            row
            for row in rows
            if row.get("evidence_tier") == 4
            and row.get("statistical_gate_passed") is True
            and row.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        ]
    if not rows:
        reasons.append("no_joint_retrieval_candidate_meets_component_cost_and_quality_gates")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": sorted(row["candidate"]["key"] for row in rows) if not reasons else [],
        "plan_hash": expected_plan_hash,
        "production_deployed": False,
        "production_savings_usd": None,
    }
