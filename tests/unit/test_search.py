from copy import deepcopy

import pytest
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.evals.datasets import digest, prepare_dataset
from cutback.evals.experiments import ModelSpec, plan_summary, run
from cutback.evals.search import candidate_portfolio, regression_gate, sample_frontier


def test_portfolio_is_deterministic_bounded_and_preserves_native_contract():
    baseline = ModelSpec(key="baseline", provider="openai", model="gpt-4.1", max_output_tokens=64)
    choices = candidate_portfolio(baseline)
    assert len(choices) == 5
    assert [(m.model, m.max_output_tokens) for m in choices] == [
        ("gpt-4.1-mini", 64),
        ("gpt-4.1", 32),
        ("gpt-4.1-mini", 32),
        ("gpt-4.1", 16),
        ("gpt-4.1-mini", 16),
    ]
    assert choices == candidate_portfolio(baseline)
    assert len({m.key for m in choices}) == 5
    for candidate in choices:
        assert candidate.instruction == baseline.instruction
        assert candidate.request_contract == baseline.request_contract
        assert candidate.provider == baseline.provider
    cheapest = baseline.model_copy(update={"model": "gpt-4.1-mini", "max_output_tokens": 16})
    with pytest.raises(ValueError, match="NO_QUALIFIED_SEARCH_VARIANT"):
        candidate_portfolio(cheapest)
    for limit in (0, 6, True):
        with pytest.raises(ValueError, match="SEARCH_LIMIT"):
            candidate_portfolio(baseline, limit=limit)


def test_search_full_budget_is_frozen_before_any_call():
    dataset = prepare_dataset(dataset_body())
    baseline = ModelSpec(key="baseline", provider="openai", model="gpt-4.1", max_output_tokens=64)
    plan = plan_for(
        dataset,
        baseline=baseline.model_dump(),
        candidates=[m.model_dump() for m in candidate_portfolio(baseline)],
        approved_providers=["openai"],
        budget={"max_usd": "20", "max_requests": 1},
    )
    summary = plan_summary(plan, dataset)
    assert summary["requests"] == summary["sample_n"] * 6
    assert not summary["fits_budget"]
    executor = SyntheticExecutor()
    with pytest.raises(ValueError, match="NO_DATA_OR_BUDGET_TOO_SMALL"):
        run(plan, dataset, executor, approval_hash=digest(plan.model_dump(mode="json")))
    assert not executor.calls


def frontier_report():
    rows = []
    decisions = []
    for key, cost, score, latency, passed in [
        ("baseline", "1", 1.0, 30, True),
        ("balanced", "0.3", 1.0, 15, True),
        ("dominated", "0.4", 0.9, 20, True),
        ("fast", "0.5", 1.0, 5, True),
        ("unsafe", "0.01", 0.1, 1, False),
    ]:
        rows.append(
            {
                "model": {"key": key, "model": "synthetic", "max_output_tokens": 32},
                "requests": 2,
                "cost_complete": True,
                "modeled_cost_usd": cost,
                "latency_ms": [latency, latency],
                "metric_min": score,
            }
        )
        if key != "baseline":
            decisions.append(
                {
                    "candidate": {"key": key},
                    "quality_gate_passed": passed,
                    "cheaper_on_sample": True,
                    "evidence_tier": 4,
                    "decision_reason": "sample_gates_passed" if passed else "quality_failed",
                }
            )
    return {
        "status": "COMPLETED",
        "billing_complete": True,
        "models": rows,
        "recommendations": decisions,
    }


def test_frontier_keeps_tradeoffs_and_excludes_failed_quality():
    report = frontier_report()
    before = deepcopy(report)
    result = sample_frontier(report)
    assert report == before
    rows = {r["key"]: r for r in result["candidates"]}
    assert rows["balanced"]["on_frontier"] and rows["fast"]["on_frontier"]
    assert rows["dominated"]["on_frontier"] is False
    assert not rows["unsafe"]["eligible"] and rows["unsafe"]["on_frontier"] is None
    assert result["production_savings_usd"] is None
    assert "not_independent" in result["scope"]


@pytest.mark.parametrize("unknown", ["cost", "latency", "billing", "quality", "status"])
def test_frontier_preserves_missing_evidence(unknown):
    report = frontier_report()
    model = report["models"][1]
    if unknown == "cost":
        model["cost_complete"] = False
    elif unknown == "latency":
        model["latency_ms"] = [1]
    elif unknown == "billing":
        report["billing_complete"] = False
    elif unknown == "quality":
        model["metric_min"] = float("nan")
    else:
        report["status"] = "FAILED"
    row = sample_frontier(report)["candidates"][1]
    assert row["on_frontier"] is None
    if unknown != "latency":
        assert not row["eligible"]


def test_ci_gate_fails_closed_for_search_stale_unknown_or_provisional_evidence():
    dataset = prepare_dataset(dataset_body())
    plan = plan_for(dataset)
    report = run(
        plan, dataset, SyntheticExecutor(), approval_hash=digest(plan.model_dump(mode="json"))
    )
    expected = report["plan"]["plan_hash"]
    assert regression_gate(report, expected_plan_hash=expected, sample_only=True)["passed"]
    assert not regression_gate(report, expected_plan_hash="a" * 64, sample_only=True)["passed"]
    assert not regression_gate(report, expected_plan_hash=expected)["passed"]
    report["recommendations"][0].update(
        evidence_tier=4,
        statistical_gate_passed=True,
        status="VERIFIED_ON_HELDOUT_DATASET",
    )
    assert regression_gate(report, expected_plan_hash=expected)["passed"]
    report["plan"]["split"] = "validation"
    assert not regression_gate(report, expected_plan_hash=expected, sample_only=True)["passed"]
