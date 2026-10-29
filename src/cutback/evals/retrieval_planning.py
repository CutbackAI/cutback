"""Automatic finite retrieval search and frozen independent confirmation. No calls."""

from typing import Any
from uuid import uuid4

from cutback.core.retrieval import RetrievalSettings, RetrievalVariant
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec
from cutback.evals.metrics import MetricSpec
from cutback.evals.retrieval import RetrievalExecutionSpec


def candidate_settings(baseline: RetrievalSettings) -> tuple[RetrievalSettings, ...]:
    """Deterministic bounded search, not a promise that any alternative improves quality."""
    raw = baseline.model_dump(mode="json")
    changes: list[dict[str, Any]] = [
        {
            "chunk_chars": max(128, baseline.chunk_chars // 2),
            "overlap_chars": min(baseline.overlap_chars, max(128, baseline.chunk_chars // 2) // 2),
        },
        {"top_k": max(1, baseline.top_k // 2)},
        {"chunk_chars": max(128, baseline.chunk_chars // 4), "overlap_chars": 0},
        {"overlap_chars": 0},
        {"rerank": "query_term_coverage"},
        {
            "chunk_chars": max(128, baseline.chunk_chars // 2),
            "overlap_chars": 0,
            "top_k": max(1, baseline.top_k // 2),
            "rerank": "query_term_coverage",
        },
    ]
    values = []
    for change in changes:
        value = RetrievalSettings.model_validate({**raw, **change})
        if value != baseline and value not in values:
            values.append(value)
    if not values:
        raise ValueError("NO_DISTINCT_RETRIEVAL_CANDIDATES")
    return tuple(values[:5])


def draft_retrieval_plan(
    dataset,
    baseline: ModelSpec,
    corpus,
    settings: RetrievalSettings,
    *,
    metric: MetricSpec,
    budget: Budget,
    candidates=None,
    assumed_usd_per_cpu_hour=None,
    max_query_bytes=1024,
    retain_case_outputs=False,
    configuration_binding=None,
):
    provenance = dataset["provenance"]
    if baseline.provider not in {"openai", "anthropic"}:
        raise ValueError("RETRIEVAL_REQUIRES_QUALIFIED_NATIVE_PROVIDER")
    assert baseline.provider in {"openai", "anthropic"}
    if not provenance.get("independent_labels"):
        raise ValueError("RETRIEVAL_SEARCH_REQUIRES_INDEPENDENT_ANSWER_LABELS")
    if provenance.get("max_input_bytes", 0) > max_query_bytes:
        raise ValueError("RETRIEVAL_DATASET_QUERY_EXCEEDS_APPROVED_BOUND")
    choices = (
        tuple(RetrievalSettings.model_validate(v) for v in candidates)
        if candidates is not None
        else candidate_settings(settings)
    )
    if not 1 <= len(choices) <= 5 or any(
        value in choices[:index] for index, value in enumerate(choices)
    ):
        raise ValueError("RETRIEVAL_CANDIDATES_MUST_BE_DISTINCT_AND_BOUNDED")
    if settings in choices:
        raise ValueError("RETRIEVAL_CANDIDATE_EQUALS_BASELINE")
    keys = ["retrieval-" + str(i) for i in range(1, 7) if "retrieval-" + str(i) != baseline.key]
    models = [baseline, *[baseline.model_copy(update={"key": key}) for key in keys[: len(choices)]]]
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=models[1:],
        metric=metric,
        budget=budget,
        approved_providers=["openai"] if baseline.provider == "openai" else ["anthropic"],
        split="validation",
        evaluator_version="deterministic-v6",
        content_nonce=uuid4(),
        retain_case_outputs=retain_case_outputs,
        retrieval_execution=RetrievalExecutionSpec(
            configuration_binding=configuration_binding,
            corpus=corpus,
            max_query_bytes=max_query_bytes,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            variants=tuple(
                RetrievalVariant(model_key=m.key, settings=s)
                for m, s in zip(models, [settings, *choices], strict=True)
            ),
        ),
    )


def confirmation_plan(prior: ExperimentPlan, report, run_id: str, budget: Budget):
    """Select on validation only; server revalidates predecessor and holdout authority."""
    if (
        prior.retrieval_execution is None
        or prior.split != "validation"
        or prior.predecessor_run_id
        or report.get("status") != "COMPLETED"
        or not report.get("billing_complete")
    ):
        raise ValueError("COMPLETED_RETRIEVAL_VALIDATION_REQUIRED")
    selected = next(
        (
            r["candidate"]["key"]
            for r in report.get("recommendations", [])
            if r.get("retrieval_automation_eligible")
        ),
        None,
    )
    if selected is None:
        raise ValueError("NO_RETRIEVAL_CANDIDATE_PASSED_COST_AND_QUALITY_GATES")
    model = next((m for m in prior.candidates if m.key == selected), None)
    if model is None:
        raise ValueError("RETRIEVAL_REPORT_CANDIDATE_NOT_IN_FROZEN_PLAN")
    raw = prior.model_dump(mode="json")
    raw.update(
        split="test",
        sample_limit=None,
        predecessor_run_id=run_id,
        candidates=[model.model_dump(mode="json")],
        budget=budget.model_dump(mode="json"),
    )
    raw["retrieval_execution"]["variants"] = [
        v.model_dump(mode="json")
        for v in prior.retrieval_execution.variants
        if v.model_key in {prior.baseline.key, selected}
    ]
    return ExperimentPlan.model_validate(raw)
