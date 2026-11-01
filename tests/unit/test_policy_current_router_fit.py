"""Current-router costs, paired native controls and preserved unobserved behavior."""

from decimal import Decimal, localcontext
from itertools import product
from uuid import uuid4

import pytest
from test_source_improvement_policy import policy

from cutback.evals.experiments import route_target
from cutback.evals.learned_routing import (
    RoutingFitOptions,
    SourcePolicyRoutingTrainingEvidence,
    fit_routes,
)
from cutback.evals.policy_routing_fit import fit_current_routes


def evidence():
    current = policy(routed=True).strategy.model_copy(update={"key": "current"})
    first = policy().strategy.model_copy(update={"key": "first"})
    second = first.model_copy(
        update={"key": "second", "instruction": "Owned second native control"}
    )
    observations = []
    for index, length in enumerate((10, 20, 70, 80)):
        for key, cost, acceptable in (
            ("current", ".2" if length < 64 else ".4", True),
            ("first", ".05", length < 64),
            ("second", ".1", True),
        ):
            observations.append(
                {
                    "case_sha256": f"{index + 1:064x}",
                    "group_sha256": f"{index + 10:064x}",
                    "input_bytes": length,
                    "model_key": key,
                    "acceptable": acceptable,
                    "known_cost_usd": cost,
                }
            )
    return SourcePolicyRoutingTrainingEvidence(
        split="train",
        dataset_sha256="a" * 64,
        source_plan_sha256="b" * 64,
        source_run_id=uuid4(),
        baseline_key="current",
        models=[current, first, second],
        observations=observations,
    )


def test_fit_matches_exhaustive_small_oracle_and_keeps_current_router_tail():
    value = evidence()
    result = fit_current_routes(value, RoutingFitOptions(max_routes=3, min_leaf_cases=1))
    by_case = {row.case_sha256 for row in value.observations}
    rows = {(row.case_sha256, row.model_key): row for row in value.observations}
    feasible = []
    for assignments in product(("first", "second"), repeat=4):
        # Two prefix leaves plus the untouched current native tail fit three routes.
        if 1 + sum(a != b for a, b in zip(assignments[:-1], assignments[1:], strict=True)) > 2:
            continue
        paired = [rows[case, key] for case, key in zip(sorted(by_case), assignments, strict=True)]
        if all(row.acceptable for row in paired):
            feasible.append(sum((row.known_cost_usd for row in paired), Decimal(0)))
    assert Decimal(result["fitted_training_native_cost_usd"]) == min(feasible) == Decimal(".30")
    assert Decimal(result["baseline_training_native_cost_usd"]) == Decimal("1.2")
    from cutback.evals.experiments import ModelSpec

    fitted = ModelSpec.model_validate(result["candidate"])
    current = value.models[0]
    for length in range(81, 129):
        assert route_target(fitted, "x" * length) == route_target(current, "x" * length)
    assert result["unobserved_input_tail_preserves_current_router"] is True
    assert result["heldout_quality_claim"] is False
    assert result["routing_extra_provider_requests"] == 0
    assert result["routing_hosting_cost_usd"] is None
    with pytest.raises(ValueError, match="PLAIN_NATIVE_REFERENCE"):
        fit_routes(value, RoutingFitOptions())


def test_an_expensive_historical_anchor_cannot_make_a_losing_current_comparison_win():
    raw = evidence().model_dump(mode="json")
    for row in raw["observations"]:
        if row["model_key"] == "current":
            row["known_cost_usd"] = "0.001"
    with pytest.raises(ValueError, match="NO_LOWER_COST_FEASIBLE_POLICY"):
        fit_current_routes(
            SourcePolicyRoutingTrainingEvidence.model_validate(raw),
            RoutingFitOptions(min_leaf_cases=1),
        )


def test_fitting_owns_money_precision_instead_of_rounding_away_a_small_known_difference():
    raw = evidence().model_dump(mode="json")
    for row in raw["observations"]:
        row["known_cost_usd"] = (
            "0.123456789012" if row["model_key"] == "current" else "0.123456789011"
        )
    value = SourcePolicyRoutingTrainingEvidence.model_validate(raw)
    with localcontext() as context:
        context.prec = 6
        result = fit_current_routes(value, RoutingFitOptions(min_leaf_cases=1))
    assert result["fitted_training_native_cost_difference_usd"] == "4E-12"


@pytest.mark.parametrize(
    "change",
    ["unknown_reference", "unknown_controls", "too_few_routes", "timeout", "outside_domain"],
)
def test_unknown_costs_bounds_and_fitting_limits_never_fabricate_a_viable_route(change):
    raw = evidence().model_dump(mode="json")
    options = RoutingFitOptions(max_routes=2, min_leaf_cases=1)
    if change == "unknown_reference":
        raw["observations"][0]["known_cost_usd"] = None
    elif change == "unknown_controls":
        for row in raw["observations"]:
            if row["model_key"] != "current":
                row["known_cost_usd"] = None
    elif change == "too_few_routes":
        for row in raw["observations"]:
            if row["model_key"] == "second" and row["input_bytes"] < 64:
                row["acceptable"] = False
    elif change == "outside_domain":
        for row in raw["observations"]:
            if row["input_bytes"] == 80:
                row["input_bytes"] = 129
    ticks = iter([0, 99])
    kwargs = {"clock": lambda: next(ticks)} if change == "timeout" else {}
    with pytest.raises(ValueError):
        fit_current_routes(
            SourcePolicyRoutingTrainingEvidence.model_validate(raw), options, **kwargs
        )
