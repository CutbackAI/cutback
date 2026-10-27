"""Adversarial release contracts use independent synthetic inputs and native fakes."""

import json
from decimal import Decimal

import pytest
from support_evaluation import dataset_body, plan_for

from cutback.evals.datasets import prepare_dataset
from cutback.evals.experiments import Execution, plan_summary, run
from cutback.scanner import scan


@pytest.mark.parametrize("asynchronous", [False, True])
def test_context_managed_native_clients_are_discovered(tmp_path, asynchronous):
    constructor = "AsyncOpenAI" if asynchronous else "OpenAI"
    code = f"from openai import {constructor}\n"
    prefix = ""
    if asynchronous:
        code += "async def work():\n"
        prefix = "    "
    code += f"{prefix}{'async ' if asynchronous else ''}with {constructor}() as client:\n"
    code += (
        f"{prefix}    {'await ' if asynchronous else ''}client.responses.create("
        "model='gpt-4.1', input='synthetic')\n"
    )
    (tmp_path / "app.py").write_text(code)
    (site,) = scan(tmp_path)["call_sites"]
    assert site["provider"] == "openai" and site["requested_model"] == "gpt-4.1"


@pytest.mark.parametrize("requirement", ["min_test_samples", "min_test_groups"])
def test_required_criterion_cannot_ignore_declared_evidence_size(requirement):
    dataset = prepare_dataset(dataset_body())
    spec = plan_for(
        dataset,
        criteria=[
            {
                "key": "required-evidence",
                "metric": {"kind": "exact_label", "minimum": 0.5, requirement: 10000},
            }
        ],
    )

    class Correct:
        def execute(self, model, text, timeout_seconds):
            return Execution("A", Decimal("0.0001"), 1)

    result = run(
        spec,
        dataset,
        Correct(),
        approval_hash=plan_summary(spec, dataset)["plan_hash"],
        independent_labels=True,
        representative=True,
    )
    decision = result["recommendations"][0]
    assert decision["status"] == "NEEDS_VALIDATION"
    assert decision["insufficient_criteria"] == ["required-evidence"]
    assert not decision["quality_gate_passed"]
    criterion = result["models"][1]["criteria"][0]
    assert not criterion["evidence_sufficient"]
    assert not criterion["passed"]


def test_incomplete_judge_votes_are_invalid_even_when_json_is_parseable():
    dataset = prepare_dataset(dataset_body())
    spec = plan_for(
        dataset,
        judge={
            "key": "judge",
            "provider": "local",
            "model": "synthetic-judge",
            "local_cost_upper_usd": "0.001",
        },
        judge_rubric="Prefer correct labels",
        judge_swap_orders=True,
        judge_require_evidence=True,
    )

    class TruncatedJudge:
        def execute(self, model, text, timeout_seconds):
            if model.key == "judge":
                return Execution(
                    json.dumps({"winner": "A", "evidence_quote": json.loads(text)["input"]}),
                    Decimal("0.0001"),
                    1,
                    complete=False,
                )
            return Execution("A", Decimal("0.0001"), 1)

    result = run(
        spec,
        dataset,
        TruncatedJudge(),
        approval_hash=plan_summary(spec, dataset)["plan_hash"],
        independent_labels=True,
    )
    judge = result["judges"][0]
    assert judge["invalid_votes"] == len(judge["votes"])
    assert not judge["health_passed"]
    assert {vote["invalid_reason"] for vote in judge["votes"]} == {"incomplete_response"}
