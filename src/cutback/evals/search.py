"""Bounded portfolios and descriptive trade-offs, never inference or tuning on tests."""

import math
from decimal import Decimal

from cutback.core.request_profiles import fingerprint
from cutback.evals.experiments import ModelSpec
from cutback.pricing import Catalog


def candidate_portfolio(baseline: ModelSpec, *, limit=5, exact_cache=None) -> list[ModelSpec]:
    """Propose known native models and smaller output bounds before reading any outcomes.

    A smaller cap is only a hypothesis. Actual usage must fall and incomplete outputs fail
    the existing worker quality gate. No invented prompt compression or free model call.
    """
    from cutback.evals.planning import VALIDATED_MODELS

    if type(limit) is not int or not 1 <= limit <= 5:
        raise ValueError("SEARCH_LIMIT_ONE_TO_FIVE_REQUIRED")
    if baseline.exact_cache is not None:
        raise ValueError("SEARCH_REQUIRES_UNCACHED_NATIVE_BASELINE")
    cache = None
    if exact_cache is not None:
        from cutback.core.exact_cache import ExactCacheExecution

        cache = ExactCacheExecution.model_validate(exact_cache)
    if baseline.provider not in VALIDATED_MODELS or baseline.routes or baseline.prompt_cache:
        raise ValueError("SEARCH_REQUIRES_QUALIFIED_NATIVE_BASELINE")
    catalog = Catalog()
    rate = catalog.rate_info(baseline.provider, baseline.model)["rate"]
    if not rate:
        raise ValueError("BASELINE_PRICE_UNKNOWN_EXPLICIT_CANDIDATE_REQUIRED")
    cheaper = [
        name
        for name in VALIDATED_MODELS[baseline.provider]
        if (other := catalog.rate_info(baseline.provider, name)["rate"])
        and Decimal(other["input"]) < Decimal(rate["input"])
        and Decimal(other["output"]) <= Decimal(rate["output"])
    ]
    choices = [(name, baseline.max_output_tokens) for name in cheaper]
    for cap in dict.fromkeys(
        [max(16, baseline.max_output_tokens // 2), max(16, baseline.max_output_tokens // 4)]
    ):
        if cap < baseline.max_output_tokens:
            choices.extend((name, cap) for name in [baseline.model, *cheaper])
    result = []
    for name, cap in choices[: limit - int(cache is not None)]:
        value = baseline.model_dump(mode="json", exclude={"key"})
        value.update(model=name, max_output_tokens=cap)
        result.append(
            ModelSpec.model_validate({**value, "key": "search-" + fingerprint(value)[:12]})
        )
    if cache is not None:
        value = baseline.model_dump(mode="json", exclude={"key"})
        value["exact_cache"] = cache.model_dump(mode="json")
        result.append(
            ModelSpec.model_validate({**value, "key": "cache-" + fingerprint(value)[:12]})
        )
    if not result:
        raise ValueError("NO_QUALIFIED_SEARCH_VARIANT_SUBMIT_REVIEWED_CANDIDATE")
    return result


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return None
    return value


def sample_frontier(report: dict) -> dict:
    """Use recorded gates and complete measured costs. Never promote evidence tiers.

    This is descriptive post-selection evidence, not independent confirmation. Unknown
    latency excludes dominance comparisons; a retained summary cannot reconstruct p95.
    """
    decisions = {r["candidate"]["key"]: r for r in report.get("recommendations", [])}
    rows = []
    complete = report.get("status") == "COMPLETED" and report.get("billing_complete") is True
    for index, result in enumerate(report.get("models", [])):
        model = result["model"]
        decision = decisions.get(model["key"], {})
        count = result.get("requests")
        count = count if type(count) is int and count > 0 else None
        cost = result.get("modeled_cost_usd") if result.get("cost_complete") is True else None
        try:
            cost = Decimal(str(cost)) if cost is not None else None
            if cost is not None and (not cost.is_finite() or cost < 0):
                cost = None
        except ArithmeticError:
            cost = None
        latencies = result.get("latency_ms", [])
        latency = (
            sum(latencies) / len(latencies)
            if count
            and len(latencies) == count
            and all(finite_number(v) is not None and v >= 0 for v in latencies)
            else None
        )
        score = finite_number(result.get("metric_min"))
        eligible = bool(
            index
            and complete
            and decision.get("quality_gate_passed") is True
            and decision.get("cheaper_on_sample") is True
            and count
            and cost is not None
            and score is not None
        )
        rows.append(
            {
                "key": model["key"],
                "model": model["model"],
                "baseline": index == 0,
                "max_output_tokens": model["max_output_tokens"],
                **(
                    {"engine": "exact_cache", "exact_cache": result.get("exact_cache")}
                    if model.get("exact_cache") is not None
                    else {}
                ),
                "sample_quality_min": score,
                "cost_per_request_usd": str(cost / count) if cost is not None and count else None,
                "mean_latency_ms": latency,
                "eligible": eligible,
                "reason": "baseline_reference"
                if index == 0
                else "run_or_billing_incomplete"
                if not complete
                else "cost_or_request_count_unknown"
                if cost is None or not count
                else decision.get("decision_reason", "candidate_decision_missing"),
                "evidence_tier": decision.get("evidence_tier"),
                "statistical_gate_passed": decision.get("statistical_gate_passed", False),
                "on_frontier": None,
            }
        )
    comparable = [r for r in rows if r["eligible"] and r["mean_latency_ms"] is not None]

    def dominates(a, b):
        left = (Decimal(a["cost_per_request_usd"]), -a["sample_quality_min"], a["mean_latency_ms"])
        right = (Decimal(b["cost_per_request_usd"]), -b["sample_quality_min"], b["mean_latency_ms"])
        return all(x <= y for x, y in zip(left, right, strict=True)) and any(
            x < y for x, y in zip(left, right, strict=True)
        )

    for row in comparable:
        row["on_frontier"] = not any(dominates(other, row) for other in comparable)
    return {
        "version": "sample-frontier-v1",
        "candidates": rows,
        "eligible_candidates": sum(r["eligible"] for r in rows),
        "scope": "descriptive_same_sample_not_independent_selection_confirmation",
        "production_savings_usd": None,
        "production_deployed": False,
        "next_action": "freeze_selected_candidate_then_independent_confirmation_and_staging",
    }


def regression_gate(report, *, expected_plan_hash, candidate=None, sample_only=False):
    """Fail closed for CI. Passing this gate never approves or deploys a change."""
    reasons = []
    if report.get("native_fixture") and not sample_only:
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    from cutback.core.evaluation_criteria import source_evaluator_eligible

    if report.get("status") != "COMPLETED" or report.get("billing_complete") is not True:
        reasons.append("run_or_accounting_incomplete")
    if report.get("plan", {}).get("plan_hash") != expected_plan_hash:
        reasons.append("frozen_plan_mismatch")
    if report.get("plan", {}).get("split") != "test":
        reasons.append("independent_test_confirmation_required")
    if not sample_only and report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    choices = [
        r
        for r in report.get("recommendations", [])
        if (candidate is None or r["candidate"]["key"] == candidate)
        and source_evaluator_eligible(r)
        and (
            r.get("evaluator_version") != "deterministic-v13"
            or r.get("criteria_binding") == report.get("criteria_binding")
        )
        and r.get("quality_gate_passed") is True
        and r.get("cheaper_on_sample") is True
    ]
    if not sample_only:
        choices = [
            r
            for r in choices
            if r.get("evidence_tier") == 4
            and r.get("statistical_gate_passed") is True
            and r.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        ]
    if not choices:
        reasons.append("no_candidate_meets_required_evidence_cost_and_quality")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": sorted(r["candidate"]["key"] for r in choices) if not reasons else [],
        "plan_hash": expected_plan_hash,
        "production_deployed": False,
        "production_savings_usd": None,
    }
