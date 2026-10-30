"""Train fitting must neither learn from holdout nor buy cheap wrong answers."""

from decimal import Decimal
from itertools import product
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.evals.datasets import digest
from cutback.evals.experiments import ModelSpec, route_target
from cutback.evals.learned_routing import RoutingFitOptions, RoutingTrainingEvidence, fit_routes


def training(lengths=(10, 10, 30, 30, 70, 70), *, cheap_passes=None, cheap_cost=None):
    models = [
        ModelSpec(key="baseline", provider="openai", model="gpt-4.1"),
        ModelSpec(key="small", provider="openai", model="gpt-4.1-mini"),
    ]
    observations = []
    for index, length in enumerate(lengths):
        for model in models:
            observations.append(
                {
                    "case_sha256": digest(index),
                    "group_sha256": digest(index),
                    "input_bytes": length,
                    "model_key": model.key,
                    "acceptable": model.key == "baseline"
                    or cheap_passes is None
                    or cheap_passes[index],
                    "known_cost_usd": "0.001"
                    if model.key == "baseline"
                    else (cheap_cost[index] if cheap_cost is not None else "0.0001"),
                }
            )
    return RoutingTrainingEvidence(
        split="train",
        dataset_sha256="a" * 64,
        source_plan_sha256="b" * 64,
        source_run_id=uuid4(),
        baseline_key="baseline",
        models=models,
        observations=observations,
    )


def options(**kwargs):
    return RoutingFitOptions(min_leaf_cases=1, min_training_groups=2, **kwargs)


def test_fit_rejects_cheap_constant_failures_and_keeps_bounded_baseline_tail():
    result = fit_routes(training(cheap_passes=[True, True, False, False, True, True]), options())
    route = ModelSpec.model_validate(result["candidate"])
    assert [route_target(route, "x" * length).key for length in [5, 10, 20, 30, 50, 70, 71]] == [
        "small",
        "small",
        "baseline",
        "baseline",
        "small",
        "small",
        "baseline",
    ]
    assert Decimal(result["fitted_training_native_cost_usd"]) == Decimal("0.0024")
    assert result["heldout_quality_claim"] is False and result["source_patch_eligible"] is False
    assert result["routing_hosting_cost_usd"] is None
    assert result["routing_extra_provider_requests"] == 0
    assert result["requires_validation_and_untouched_confirmation"] is True
    with pytest.raises(ValueError, match="NO_APPROVED_ROUTE"):
        route_target(route, "x" * (8192 - 4096 + 1))


def test_equal_lengths_cannot_be_routed_to_different_models_to_memorize_answers():
    result = fit_routes(training(cheap_passes=[True, False, True, True, True, True]), options())
    route = ModelSpec.model_validate(result["candidate"])
    assert route_target(route, "x" * 10).key == "baseline"
    assert route_target(route, "x" * 30).key == "small"


def test_unknown_cost_is_never_zero_and_unknown_baseline_abstains():
    evidence = training(cheap_cost=[None, None, "0.0001", "0.0001", "0.0001", "0.0001"])
    route = ModelSpec.model_validate(fit_routes(evidence, options())["candidate"])
    assert route_target(route, "x" * 10).key == "baseline"
    evidence.observations[0].known_cost_usd = None
    with pytest.raises(ValueError, match="BASELINE_COST_UNKNOWN"):
        fit_routes(evidence, options())


def test_fit_requires_paired_training_and_declared_group_coverage():
    evidence = training()
    raw = evidence.model_dump(mode="json")
    for split in ["test", "validation"]:
        with pytest.raises(ValidationError):
            RoutingTrainingEvidence.model_validate({**raw, "split": split})
    with pytest.raises(ValidationError, match="COMPLETE_PAIRING"):
        RoutingTrainingEvidence.model_validate({**raw, "observations": raw["observations"][:-1]})
    with pytest.raises(ValidationError, match="DUPLICATE_OR_UNKNOWN"):
        RoutingTrainingEvidence.model_validate({**raw, "observations": raw["observations"] * 2})
    for row in evidence.observations:
        row.group_sha256 = "c" * 64
    with pytest.raises(ValueError, match="GROUPS_INSUFFICIENT"):
        fit_routes(evidence, options())


def test_no_feasible_saving_and_small_leaves_abstain():
    with pytest.raises(ValueError, match="NO_LOWER_COST_FEASIBLE_POLICY"):
        fit_routes(training(cheap_passes=[False] * 6), options())
    with pytest.raises(ValueError, match="NO_LOWER_COST_FEASIBLE_POLICY"):
        fit_routes(training(cheap_cost=["0.002"] * 6), options())
    with pytest.raises(ValueError, match="NO_FEASIBLE_PARTITION"):
        fit_routes(training(), RoutingFitOptions(min_leaf_cases=7, min_training_groups=2))


def test_canonical_policy_is_independent_of_input_iteration_order():
    evidence = training(cheap_passes=[True, True, False, False, True, True])
    first = fit_routes(evidence, options())
    evidence.observations.reverse()
    evidence.models.reverse()
    second = fit_routes(evidence, options())
    assert first["candidate"] == second["candidate"]
    assert first["fitted_training_native_cost_usd"] == second["fitted_training_native_cost_usd"]


def test_dynamic_program_matches_exhaustive_small_partition_oracle():
    # An independent exhaustive assignment check catches greedy-boundary errors.
    for accepted in product([False, True], repeat=4):
        evidence = training(lengths=(10, 20, 30, 40), cheap_passes=accepted)
        if not any(accepted):
            continue
        possible = []
        for assignment in product(["baseline", "small"], repeat=4):
            if any(key == "small" and not accepted[index] for index, key in enumerate(assignment)):
                continue
            leaves = 1 + sum(assignment[i] != assignment[i - 1] for i in range(1, 4))
            if leaves + (assignment[-1] != "baseline") > 3:
                continue
            possible.append(
                sum(
                    (
                        Decimal("0.0001") if key == "small" else Decimal("0.001")
                        for key in assignment
                    ),
                    Decimal(0),
                )
            )
        if min(possible) >= Decimal("0.004"):
            with pytest.raises(ValueError, match="NO_LOWER_COST_FEASIBLE_POLICY"):
                fit_routes(evidence, options(max_routes=3))
        else:
            result = fit_routes(evidence, options(max_routes=3))
            assert Decimal(result["fitted_training_native_cost_usd"]) == min(possible)


def test_routing_uses_utf8_bytes_and_respects_qualified_target_input_bounds():
    evidence = training(lengths=(10, 20, 30, 40))
    evidence.models[1].max_input_tokens = 4116
    result = fit_routes(evidence, options())
    route = ModelSpec.model_validate(result["candidate"])
    assert route_target(route, "é" * 10).key == "small"
    assert route_target(route, "é" * 11).key == "baseline"


def test_fit_has_a_hard_work_deadline_without_making_provider_requests():
    ticks = iter([0, 31])
    with pytest.raises(ValueError, match="ROUTING_FIT_COMPUTE_BOUND"):
        fit_routes(training(), options(), clock=lambda: next(ticks))
