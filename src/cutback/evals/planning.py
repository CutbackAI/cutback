"""Automatic constrained candidate planning from metadata. Never executes inference."""

from decimal import Decimal

from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, provider_models
from cutback.evals.metrics import MetricSpec
from cutback.pricing import Catalog

VALIDATED_MODELS = {
    "openai": ("gpt-4.1", "gpt-4.1-mini"),
    "anthropic": ("claude-sonnet-4-6", "claude-haiku-4-5"),
}


def draft_plan(
    dataset,
    baseline: ModelSpec,
    *,
    candidates=None,
    minimum=None,
    metric_kind=None,
    max_usd="0",
    max_requests=100,
    repeats=1,
    max_wall_seconds=300,
    pilot_samples=0,
    pilot_minimum=None,
):
    provenance = dataset["provenance"]
    catalog = Catalog()
    if candidates is None:
        baseline_rate = catalog.rate_info(baseline.provider, baseline.model)["rate"]
        if not baseline_rate:
            raise ValueError("BASELINE_PRICE_UNKNOWN_EXPLICIT_CANDIDATE_REQUIRED")
        candidates = []
        for name in VALIDATED_MODELS.get(baseline.provider, ()):
            rate = catalog.rate_info(baseline.provider, name)["rate"]
            if (
                rate
                and Decimal(rate["input"]) < Decimal(baseline_rate["input"])
                and Decimal(rate["output"]) <= Decimal(baseline_rate["output"])
            ):
                candidates.append(
                    baseline.model_copy(
                        update={"key": "candidate-" + str(len(candidates) + 1), "model": name}
                    )
                )
    if not candidates:
        raise ValueError("NO_COMPATIBLE_CHEAPER_MODEL_SUBMIT_REVIEWED_PROMPT_VARIANT")
    inferred = provenance.get("task_inference", {}).get("suggested_metric", "nonempty")
    kind = metric_kind or inferred
    independent = provenance["independent_labels"] and minimum is not None
    semantic = kind in {
        "exact_label",
        "field_f1",
        "retrieval_recall",
        "ndcg",
        "rank_agreement",
        "numeric_match",
    }
    if semantic and not independent:
        kind = "nonempty"
    if minimum is None:
        kind = "nonempty"
    maximum_bytes = provenance.get("max_input_bytes", 0)
    for model in [baseline, *candidates]:
        if (
            maximum_bytes
            and maximum_bytes + len(model.instruction.encode()) + 4096 > model.max_input_tokens
        ):
            raise ValueError("DATASET_EXCEEDS_MODEL_INPUT_BOUND")
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=candidates,
        metric=MetricSpec(kind=kind, minimum=minimum if minimum is not None else 1),
        budget=Budget(
            **{
                "max_usd": max_usd,
                "max_requests": max_requests,
                "max_wall_seconds": max_wall_seconds,
            }
        ),
        approved_providers=sorted(
            {
                target.provider
                for model in [baseline, *candidates]
                for target in provider_models(model)
                if target.provider != "router"
            }
        ),
        repeats=repeats,
        pilot_samples=pilot_samples,
        pilot_minimum=pilot_minimum,
    )
