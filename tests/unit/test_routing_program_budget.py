"""Every fallback and untouched case remains reserved before program approval."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from cutback.core.input_routing import RoutingTrainingExecution
from cutback.core.routing_producer import RoutingSourceBinding
from cutback.evals.campaigns import CampaignBudget
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, RouteSpec, upper_cost
from cutback.evals.metrics import MetricSpec
from cutback.evals.routing_programs import (
    RoutingProgram,
    compile_routing_program,
    program_static_plan,
    selected_controls,
)


def program(*, controls=1, requests=1000):
    baseline = ModelSpec(
        key="source-baseline",
        provider="openai",
        model="gpt-4.1",
        instruction="Return the owned A or B label.",
        max_output_tokens=64,
    )
    policy = ModelSpec(
        key="source-policy",
        provider="router",
        model="input-size-v1",
        routes=[RouteSpec(max_input_bytes=4000, target=baseline)],
    )
    source = RoutingSourceBinding(
        producer_file="app.py",
        producer_sha256="a" * 64,
        settings_file="routing.json",
        settings_sha256="b" * 64,
        source_tree_hash="c" * 64,
        commit="d" * 40,
        configuration_key=policy.key,
        configuration_model=policy.model,
        policy_sha256=digest(policy.model_dump(mode="json")),
        baseline_sha256=digest(baseline.model_dump(mode="json")),
        max_input_bytes=4000,
    )
    candidates = [
        baseline.model_copy(
            update={
                "key": "native-control-" + str(index),
                "model": "gpt-4.1-mini",
                "max_output_tokens": 64 if index == 0 else 32,
            }
        )
        for index in range(controls)
    ]
    template = ExperimentPlan(
        workflow="owned-routing",
        dataset_hash="e" * 64,
        baseline=baseline,
        candidates=candidates,
        metric=MetricSpec(kind="exact_label", minimum=1, min_test_samples=4, min_test_groups=3),
        approved_providers=["openai"],
        budget=Budget(max_usd="10", max_requests=1000),
        split="train",
        evaluator_version="deterministic-v16",
        retain_case_outputs=True,
        content_nonce=uuid4(),
        routing_execution=RoutingTrainingExecution(source=source),
    )
    specification = RoutingProgram(
        dataset_id=uuid4(),
        baseline_snapshot_id=uuid4(),
        environment="staging",
        template=template,
        budget=CampaignBudget(max_usd="10", max_requests=requests),
        conservative_savings_per_success_usd="0.01",
        forecast_successful_tasks=10000,
    )
    dataset = {
        "immutable_hash": template.dataset_hash,
        "items": [
            {"split": split, "case": {"input": f"Owned {split} item {index}", "gold": "A"}}
            for split, number in (("train", 6), ("validation", 4), ("test", 4))
            for index in range(number)
        ],
    }
    return specification, dataset


def compile(specification, dataset, cost=Decimal("1"), **options):
    return compile_routing_program(specification, dataset, cost, independent_labels=True, **options)


def test_reserve_static_training_routed_validation_and_full_confirmation():
    specification, dataset = program()
    result = compile(specification, dataset)
    assert result["eligible"] and result["max_requests"] == 36
    baseline, candidate = specification.template.baseline, specification.template.candidates[0]
    assert (
        Decimal(result["native_phase_upper_usd"]["training_if_controls_fail"])
        == (upper_cost(baseline) + upper_cost(candidate)) * 6
    )
    assert Decimal(result["native_phase_upper_usd"]["untouched_confirmation"]) == (
        upper_cost(baseline) * 2 * 4
    )
    assert result["fit_compute_usd"] is result["routing_hosting_usd"] is None
    assert result["full_task_cost_advantage"] is None
    assert result["production_changed"] is False


def test_narrow_controls_without_cutting_any_evidence_or_confirmation():
    specification, dataset = program(controls=2, requests=36)
    result = compile(specification, dataset)
    assert result["eligible"] and result["selected_control_keys"] == ["native-control-0"]
    assert result["omitted_control_keys"] == ["native-control-1"]
    assert result["dataset_split_counts"] == {"train": 6, "validation": 4, "test": 4}
    assert result["confirmation_sample_reduction"] is False
    assert len(selected_controls(specification, result)) == 1
    assert len(specification.template.candidates) == 2
    for keys in (["native-control-1"], [], ["unapproved"]):
        with pytest.raises(ValueError, match="SELECTED_CONTROLS_CHANGED"):
            selected_controls(specification, {**result, "selected_control_keys": keys})


def test_a_static_only_budget_cannot_silently_shrink_the_fallback_or_holdout():
    specification, dataset = program(requests=35)
    result = compile(specification, dataset)
    assert not result["eligible"] and result["max_requests"] == 0
    assert result["worst_case_native_usd"] is None
    assert result["reasons"] == ["complete_program_exceeds_opportunity_or_request_cap"]


@pytest.mark.parametrize("missing", ["baseline", "volume", "savings", "labels"])
def test_missing_economics_or_label_declaration_abstains(missing):
    specification, dataset = program()
    if missing in {"volume", "savings"}:
        field = (
            "forecast_successful_tasks"
            if missing == "volume"
            else "conservative_savings_per_success_usd"
        )
        specification = RoutingProgram.model_validate(
            {
                **specification.model_dump(mode="json"),
                field: None,
            }
        )
    result = compile_routing_program(
        specification,
        dataset,
        None if missing == "baseline" else Decimal("1"),
        independent_labels=missing != "labels",
    )
    assert not result["eligible"] and result["max_requests"] == 0
    assert result["reasons"]


def test_static_child_preserves_the_entire_source_native_frame_and_criteria():
    specification, dataset = program()
    before = digest(specification.model_dump(mode="json"))
    receipt = compile(specification, dataset)
    nonce, budget = uuid4(), Budget(max_usd="1", max_requests=100)
    child = program_static_plan(
        specification, selected_controls(specification, receipt), nonce=nonce, budget=budget
    )
    assert child.baseline == specification.template.baseline
    assert child.candidates == specification.template.candidates
    assert child.metric == specification.template.metric
    assert child.criteria == specification.template.criteria
    assert child.dataset_hash == specification.template.dataset_hash
    assert child.price_sha256 == specification.template.price_sha256
    assert child.routing_execution is None and child.split == "validation"
    assert child.content_nonce == nonce and child.retain_case_outputs is True
    assert digest(specification.model_dump(mode="json")) == before


def test_declared_input_and_case_limits_are_never_clipped_to_fit_a_budget():
    specification, dataset = program()
    dataset["items"][0]["case"]["input"] = "x" * 4001
    with pytest.raises(ValueError, match="BOUNDED_LABELED_SOURCE_TEXT"):
        compile(specification, dataset)
    dataset["items"] = dataset["items"][1:] * 100
    with pytest.raises(ValueError, match="DATASET_CASE_BOUND"):
        compile(specification, dataset)


def test_compilation_does_not_inherit_application_decimal_precision():
    specification, dataset = program()
    expected = compile(specification, dataset)
    with localcontext() as context:
        context.prec = 2
        assert compile(specification, dataset) == expected
    with pytest.raises(ValueError, match="FINITE_DECIMAL"):
        compile(specification, dataset, cost=1.0)
