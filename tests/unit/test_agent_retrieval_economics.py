"""Retrieval automation cannot rank native-only prices as full component economics."""

from copy import deepcopy
from decimal import Context, localcontext

import pytest

from cutback.agent_retrieval import component_frontier, regression_gate


def report():
    models = [
        {
            "model": {"key": key, "model": "gpt-4.1-mini", "max_output_tokens": 128},
            "requests": 3,
            "cost_complete": True,
            "modeled_cost_usd": native,
            "latency_ms": [1, 1, 1],
            "metric_min": 1,
        }
        for key, native in (("baseline", "3"), ("retrieval-1", "1"))
    ]
    return {
        "status": "COMPLETED",
        "billing_complete": True,
        "plan": {"plan_hash": "a" * 64, "split": "test"},
        "lineage": {"fresh_test_claim": True},
        "models": models,
        "retrieval": {
            "version": "cutback.retrieval-execution.v1",
            "models": {
                "baseline": {"modeled_component_cost_usd": "6"},
                "retrieval-1": {"modeled_component_cost_usd": "4.5"},
            },
        },
        "recommendations": [
            {
                "candidate": models[1]["model"],
                "evaluator_version": "deterministic-v6",
                "quality_gate_passed": True,
                "cheaper_on_sample": True,
                "retrieval_automation_eligible": True,
                "evidence_tier": 4,
                "statistical_gate_passed": True,
                "status": "VERIFIED_ON_HELDOUT_DATASET",
            }
        ],
    }


def test_frontier_uses_complete_components_and_is_independent_of_callers_decimal_context():
    value = report()
    original = deepcopy(value)
    with localcontext(Context(prec=1, Emax=2, Emin=-2)):
        result = component_frontier(value)
    assert [row["cost_per_request_usd"] for row in result["candidates"]] == ["2", "1.5"]
    assert result["eligible_candidates"] == 1
    assert result["latency_scope"] == "native_request_only_not_end_to_end_retrieval"
    assert value == original
    value["retrieval"]["models"]["retrieval-1"]["modeled_component_cost_usd"] = None
    value["recommendations"][0]["retrieval_automation_eligible"] = False
    result = component_frontier(value)
    assert result["eligible_candidates"] == 0
    assert result["candidates"][1]["cost_per_request_usd"] is None


@pytest.mark.parametrize("change", ["cpu_unknown", "lineage", "split", "evaluator", "hash"])
def test_gate_rejects_incomplete_or_wrong_evidence_even_for_sample_review(change):
    value = report()
    if change == "cpu_unknown":
        value["recommendations"][0]["retrieval_automation_eligible"] = False
    elif change == "lineage":
        value["lineage"] = {}
    elif change == "split":
        value["plan"]["split"] = "validation"
    elif change == "evaluator":
        value["recommendations"][0]["evaluator_version"] = "deterministic-v3"
    else:
        value["plan"]["plan_hash"] = "b" * 64
    checked = regression_gate(value, expected_plan_hash="a" * 64, sample_only=True)
    assert not checked["passed"] and not checked["candidate_keys"]


def test_gate_distinguishes_synthetic_review_and_population_evidence():
    value = report()
    assert regression_gate(value, expected_plan_hash="a" * 64)["passed"]
    value["native_fixture"] = True
    assert not regression_gate(value, expected_plan_hash="a" * 64)["passed"]
    assert regression_gate(value, expected_plan_hash="a" * 64, sample_only=True)["passed"]
