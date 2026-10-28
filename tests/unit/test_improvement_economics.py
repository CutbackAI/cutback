from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from cutback.core.improvement_economics import (
    ImprovementAssumptions,
    OptimizationRunCost,
    SearchEffortInput,
    choose_search_effort,
    compare_improvement_economics,
)
from cutback.core.task_outcomes import (
    NativeTaskCostEvidence,
    TaskEconomicsSummary,
    TaskOutcomeInput,
    summarize_task_economics,
    task_economics,
)


def summary(successes, failures, per_task="1", *, expected=True):
    rows = []
    for outcome in ["success"] * successes + ["failure"] * failures:
        task = TaskOutcomeInput(
            task_id=uuid4(),
            result_id=uuid4(),
            environment="staging",
            trace_id=uuid4().hex,
            workflow="synthetic-task",
            service="synthetic-test",
            occurred_at=datetime.now(UTC),
            outcome=outcome,
            source="synthetic-check",
            source_version="1",
            basis="deterministic_evaluation",
            expected_native_attempts=0,
            other_costs_declared_complete=True,
            components=[{"key": "compute", "kind": "compute", "amount_usd": per_task}],
        )
        rows.append(
            task_economics(
                task,
                NativeTaskCostEvidence(
                    observed_attempts=0,
                    priced_attempts=0,
                    trace_retained=True,
                    trace_integrity_complete=True,
                ),
            )
        )
    return TaskEconomicsSummary.model_validate(
        summarize_task_economics(rows, expected_tasks=successes + failures if expected else None)
    )


def assumptions(**changes):
    return ImprovementAssumptions(
        all_optimization_runs_declared_complete=True,
        other_optimization_costs_declared_complete=True,
        min_tasks_per_window=1,
        forecast_successful_tasks=100,
        **changes,
    )


def run(**changes):
    return OptimizationRunCost.model_validate(
        {
            "run_id": uuid4(),
            "status": "COMPLETED",
            "requests_reserved": 2,
            "known_cost_requests": 2,
            "known_cost_subtotal_usd": "10",
            "ledger_version": 1,
            "uncertain_or_reserved_calls": 0,
            **changes,
        }
    )


def test_cheaper_but_worse_never_passes_descriptive_outcome_gate():
    result = compare_improvement_economics(
        summary(20, 0, "2"), summary(10, 10, "0.5"), [run()], assumptions()
    )
    assert Decimal(result["modeled_cost_per_success_delta_usd"]) == 1
    assert result["decision"] == "abstain_or_reject"
    assert "declared_outcome_regression" in result["reasons"]
    assert result["descriptive_outcome_non_regression"] is False
    assert result["production_savings_usd"] is None and not result["deployment_authorized"]


def test_proposer_judge_search_overhead_can_exceed_horizon_benefit():
    policy = assumptions(
        other_optimization_costs=[{"key": "training", "kind": "compute", "amount_usd": "1000"}]
    )
    result = compare_improvement_economics(
        summary(10, 0, "2"), summary(10, 0, "1"), [run()], policy
    )
    assert result["declared_optimization_total_usd"] == "1010"
    assert result["forecast_break_even_successful_tasks"] == "1010"
    assert Decimal(result["forecast_net_modeled_benefit_usd"]) == -910
    assert "declared_horizon_does_not_repay_optimization_cost" in result["reasons"]


@pytest.mark.parametrize(
    "changed",
    [
        {"known_cost_requests": 1},
        {"ledger_version": None},
        {"uncertain_or_reserved_calls": 1},
        {"status": "RUNNING"},
        {"known_cost_requests": None},
    ],
)
def test_incomplete_or_crashed_operations_keep_total_optimization_cost_unknown(changed):
    result = compare_improvement_economics(
        summary(10, 0, "2"), summary(10, 0, "1"), [run(**changed)], assumptions()
    )
    assert result["known_optimization_run_subtotal_usd"] == "10"
    assert result["declared_optimization_total_usd"] is None
    assert "optimization_overhead_incomplete" in result["reasons"]
    assert result["forecast_break_even_successful_tasks"] is None


def test_unknown_tool_cost_or_window_coverage_never_becomes_zero():
    policy = assumptions(other_optimization_costs=[{"key": "retrieval-index", "kind": "retrieval"}])
    result = compare_improvement_economics(
        summary(10, 0, "2", expected=False), summary(10, 0, "1"), [run()], policy
    )
    assert result["modeled_cost_per_success_delta_usd"] is None
    assert result["declared_optimization_total_usd"] is None
    assert result["known_other_optimization_subtotal_usd"] is None
    assert result["decision"] == "abstain_or_reject"


def test_only_independent_confirmation_is_prepared_and_duplicate_run_costs_rejected():
    result = compare_improvement_economics(
        summary(10, 0, "2"), summary(10, 0, "1"), [run()], assumptions()
    )
    assert result["decision"] == "prepare_independent_confirmation" and result["reasons"] == []
    assert result["causal_attribution"] == "not_established"
    duplicate = run()
    with pytest.raises(ValueError, match="UNIQUE"):
        compare_improvement_economics(
            summary(1, 0), summary(1, 0), [duplicate, duplicate], assumptions()
        )


def test_search_effort_abstains_unknown_or_too_small_opportunity_without_calls():
    unknown = choose_search_effort(SearchEffortInput(owner_spend_cap_usd="10"))
    assert unknown["decision"] == "abstain" and unknown["candidate_limit"] == 0
    tiny = SearchEffortInput(
        conservative_savings_per_success_usd="0.01",
        forecast_successful_tasks=10,
        fixed_proposer_upper_cost_usd="0.01",
        per_candidate_upper_cost_usd="1",
        owner_spend_cap_usd="10",
    )
    result = choose_search_effort(tiny)
    assert result["decision"] == "abstain" and result["provider_calls"] == 0
    assert not result["paid_calls_authorized"]


def test_search_effort_accounts_fixed_proposer_and_all_candidate_work_under_owner_cap():
    result = choose_search_effort(
        SearchEffortInput(
            conservative_savings_per_success_usd="1",
            forecast_successful_tasks=100,
            fixed_proposer_upper_cost_usd="2",
            per_candidate_upper_cost_usd="3",
            owner_spend_cap_usd="9",
        )
    )
    assert result["decision"] == "prepare_frozen_search_plan" and result["candidate_limit"] == 2
    assert Decimal(result["suggested_total_spend_cap_usd"]) == 9
    assert not result["paid_calls_authorized"] and result["provider_calls"] == 0


def test_explicit_zero_search_cost_is_preserved_without_authorizing_a_call():
    result = choose_search_effort(
        SearchEffortInput(
            conservative_savings_per_success_usd="1",
            forecast_successful_tasks=100,
            fixed_proposer_upper_cost_usd="0",
            per_candidate_upper_cost_usd="0",
            owner_spend_cap_usd="0",
        )
    )
    assert result["candidate_limit"] == 5
    assert Decimal(result["suggested_total_spend_cap_usd"]) == 0
    assert (
        result["decision"] == "prepare_frozen_search_plan" and not result["paid_calls_authorized"]
    )
