import json

import pytest
from support_evaluation import dataset_body, plan_for

from cutback.evals.datasets import digest, prepare_dataset
from cutback.evals.experiments import Execution, plan_summary, run
from cutback.evals.metrics import MetricSpec, evaluate
from cutback.evals.recipes import compile_recipe


def test_json_field_criteria_are_semantic_only_with_independent_gold():
    cases = [{"gold": {"priority": "urgent"}}, {"gold": {"priority": "normal"}}]
    spec = MetricSpec(kind="exact_label", output_field="priority", minimum=1)
    result = evaluate(cases, ['{"priority":"normal"}', "broken"], spec, independent_labels=True)
    assert result["value"] == 0 and result["failures"] == 1
    assert result["evidence_tier"] == 4
    assert (
        evaluate(
            cases,
            ['{"priority":"urgent"}', '{"priority":"normal"}'],
            spec,
            independent_labels=False,
        )["evidence_tier"]
        == 1
    )


def test_required_criterion_overrules_good_average_and_freezes_plan():
    from decimal import Decimal

    dataset = prepare_dataset(dataset_body())
    spec = plan_for(
        dataset,
        criteria=[
            {
                "key": "strict",
                "metric": {"kind": "allowed_label", "allowed_labels": ["B"], "minimum": 1},
            }
        ],
    )

    class Executor:
        def execute(self, model, text, timeout_seconds):
            return Execution("A", Decimal("0.0001"), 1)

    result = run(
        spec,
        dataset,
        Executor(),
        approval_hash=plan_summary(spec, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert result["recommendations"][0]["failed_criteria"] == ["strict"]
    assert not result["recommendations"][0]["quality_gate_passed"]
    assert spec.model_copy(update={"retain_case_outputs": True}).model_dump() != spec.model_dump()


def test_judge_both_orders_and_quote_checks_are_budgeted():
    from decimal import Decimal

    dataset = prepare_dataset(dataset_body())
    spec = plan_for(
        dataset,
        judge={
            "key": "judge",
            "provider": "local",
            "model": "judge",
            "local_cost_upper_usd": "0.001",
        },
        judge_rubric="Prefer accurate labels",
        judge_swap_orders=True,
        judge_require_evidence=True,
    )

    class Executor:
        def execute(self, model, text, timeout_seconds):
            return Execution(
                '{"winner":"A","evidence_quote":"invented quote"}' if model.key == "judge" else "A",
                Decimal("0.0001"),
                1,
            )

    summary = plan_summary(spec, dataset)
    result = run(
        spec, dataset, Executor(), approval_hash=summary["plan_hash"], independent_labels=True
    )
    assert result["requests"] == summary["requests"]
    assert result["judges"][0]["invalid_votes"] == 2 * summary["sample_n"]
    assert not result["judges"][0]["health_passed"]
    assert not result["recommendations"][0]["quality_gate_passed"]
    assert result["recommendations"][0]["decision_reason"] == "judge_health_gate_failed"


def test_yaml_compile_never_executes_and_preserves_named_contract(tmp_path):
    dataset = prepare_dataset(dataset_body())
    specification = plan_for(dataset)
    source = tmp_path / "experiment.yaml"
    source.write_text(
        json.dumps(
            {"dataset_id": "synthetic-id", "specification": specification.model_dump(mode="json")}
        )
    )
    destination = tmp_path / "plan.json"
    assert compile_recipe(source, destination)["provider_calls"] == 0
    assert digest(json.loads(destination.read_text())["specification"]) == digest(
        specification.model_dump(mode="json")
    )
    with pytest.raises(ValueError, match="EXISTS"):
        compile_recipe(source, destination)


def test_paired_deltas_preserve_pairing_and_unknown_single_group_interval():
    from cutback.evals.comparisons import paired_delta

    cases = [{"gold": "A"}, {"gold": "A"}, {"gold": "B"}]
    metric = MetricSpec(kind="exact_label", minimum=0.9)
    result = paired_delta(cases, ["B", "A", "B"], ["A", "B", "B"], ["one"] * 3, metric)
    assert (result["improved"], result["regressed"], result["tied"]) == (1, 1, 1)
    assert result["mean_case_score_difference"] == 0
    assert result["confidence_95"] == {"low": None, "high": None}
    assert "not_population" in result["scope"]
