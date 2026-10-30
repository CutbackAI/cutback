"""Finite input-compression candidates and independent confirmation; never inference."""

from typing import Any
from uuid import uuid4

from cutback.core.compression import CompressionSettings, CompressionVariant
from cutback.evals.compression import CompressionExecutionSpec
from cutback.evals.experiments import ExperimentPlan


def candidate_settings(baseline: CompressionSettings):
    raw = baseline.model_dump(mode="json")
    values = []
    changes: list[dict[str, Any]] = [
        {"mode": "deduplicate_lines"},
        {"mode": "query_lines", "max_context_bytes": max(64, baseline.max_context_bytes // 2)},
        {"mode": "query_lines", "max_context_bytes": max(64, baseline.max_context_bytes // 4)},
    ]
    for change in changes:
        value = CompressionSettings.model_validate({**raw, **change})
        if value != baseline and value not in values:
            values.append(value)
    if not values:
        raise ValueError("NO_DISTINCT_COMPRESSION_CANDIDATE")
    return tuple(values)


def draft_compression_plan(
    dataset,
    baseline,
    settings,
    *,
    metric,
    budget,
    candidates=None,
    assumed_usd_per_cpu_hour=None,
    max_query_bytes=1024,
    retain_case_outputs=False,
    configuration_binding=None,
):
    if not dataset["provenance"].get("independent_labels"):
        raise ValueError("COMPRESSION_SEARCH_REQUIRES_INDEPENDENT_TASK_LABELS")
    choices = (
        tuple(CompressionSettings.model_validate(v) for v in candidates)
        if candidates is not None
        else candidate_settings(settings)
    )
    if (
        not 1 <= len(choices) <= 5
        or settings in choices
        or any(value in choices[:index] for index, value in enumerate(choices))
    ):
        raise ValueError("COMPRESSION_CANDIDATES_MUST_BE_DISTINCT_AND_BOUNDED")
    keys = ["compression-" + str(i) for i in range(1, 7) if "compression-" + str(i) != baseline.key]
    models = [baseline, *[baseline.model_copy(update={"key": k}) for k in keys[: len(choices)]]]
    return ExperimentPlan(
        workflow=dataset["workflow"],
        dataset_hash=dataset["hash"],
        baseline=baseline,
        candidates=models[1:],
        metric=metric,
        budget=budget,
        approved_providers=[baseline.provider],
        split="validation",
        evaluator_version="deterministic-v7",
        content_nonce=uuid4(),
        retain_case_outputs=retain_case_outputs,
        compression_execution=CompressionExecutionSpec(
            configuration_binding=configuration_binding,
            max_query_bytes=max_query_bytes,
            assumed_usd_per_cpu_hour=assumed_usd_per_cpu_hour,
            variants=tuple(
                CompressionVariant(model_key=m.key, settings=s)
                for m, s in zip(models, [settings, *choices], strict=True)
            ),
        ),
    )


def confirmation_plan(prior, report, run_id, budget):
    if (
        prior.compression_execution is None
        or prior.split != "validation"
        or prior.predecessor_run_id
        or report.get("status") != "COMPLETED"
        or not report.get("billing_complete")
    ):
        raise ValueError("COMPLETED_COMPRESSION_VALIDATION_REQUIRED")
    selected = next(
        (
            r["candidate"]["key"]
            for r in report.get("recommendations", [])
            if r.get("compression_automation_eligible")
        ),
        None,
    )
    if selected is None:
        raise ValueError("NO_COMPRESSION_CANDIDATE_PASSED_COST_AND_QUALITY_GATES")
    model = next((m for m in prior.candidates if m.key == selected), None)
    if model is None:
        raise ValueError("COMPRESSION_REPORT_CANDIDATE_NOT_IN_FROZEN_PLAN")
    raw = prior.model_dump(mode="json")
    raw.update(
        split="test",
        sample_limit=None,
        predecessor_run_id=run_id,
        candidates=[model.model_dump(mode="json")],
        budget=budget.model_dump(mode="json"),
    )
    raw["compression_execution"]["variants"] = [
        v.model_dump(mode="json")
        for v in prior.compression_execution.variants
        if v.model_key in {prior.baseline.key, selected}
    ]
    return ExperimentPlan.model_validate(raw)
