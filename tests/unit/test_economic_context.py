"""Embedding applications cannot change reservations or task economics via Decimal state."""

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Context, Inexact, getcontext, localcontext
from uuid import uuid4

import pytest
from support_retrieval import fixture

from cutback.core.improvement_economics import (
    ImprovementAssumptions,
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
from cutback.evals.experiments import plan_summary, upper_cost


@pytest.mark.parametrize(
    "operation",
    ["native_reservation", "plan_reservation", "task", "search", "window", "comparison"],
)
def test_money_and_spend_bounds_are_independent_of_embedding_decimal_state(operation):
    plan, dataset = fixture()
    task = TaskOutcomeInput(
        task_id=uuid4(),
        result_id=uuid4(),
        environment="staging",
        trace_id=uuid4().hex,
        workflow="owned",
        service="owned",
        occurred_at=datetime.now(UTC),
        outcome="success",
        source="synthetic",
        source_version="1",
        basis="deterministic_evaluation",
        expected_native_attempts=1,
        other_costs_declared_complete=True,
        components=[{"key": "hosting", "kind": "hosting", "amount_usd": "1234.123456789012"}],
    )
    evidence = NativeTaskCostEvidence(
        observed_attempts=1,
        priced_attempts=1,
        known_modeled_subtotal_usd="7654.765432109876",
        trace_retained=True,
        trace_integrity_complete=True,
    )
    effort = SearchEffortInput(
        conservative_savings_per_success_usd="0.123456789012",
        forecast_successful_tasks=1000000,
        per_candidate_upper_cost_usd="0.012345678901",
        fixed_proposer_upper_cost_usd="0.001",
        owner_spend_cap_usd="100",
        max_fraction_of_opportunity="0.1",
    )
    row = task_economics(task, evidence)
    baseline = TaskEconomicsSummary.model_validate(
        summarize_task_economics([row], expected_tasks=1)
    )
    candidate_row = task_economics(
        task,
        evidence.model_copy(
            update={"known_modeled_subtotal_usd": evidence.known_modeled_subtotal_usd / 2}
        ),
    )
    candidate = TaskEconomicsSummary.model_validate(
        summarize_task_economics([candidate_row], expected_tasks=1)
    )
    assumptions = ImprovementAssumptions(
        all_optimization_runs_declared_complete=True,
        other_optimization_costs_declared_complete=True,
        forecast_successful_tasks=1000000,
        min_tasks_per_window=1,
    )
    actions = {
        "native_reservation": lambda: upper_cost(plan.baseline),
        "plan_reservation": lambda: plan_summary(plan, dataset),
        "task": lambda: task_economics(task, evidence),
        "search": lambda: choose_search_effort(effort),
        "window": lambda: summarize_task_economics([row], expected_tasks=1),
        "comparison": lambda: compare_improvement_economics(baseline, candidate, [], assumptions),
    }
    expected = actions[operation]()
    hostile = Context(prec=2, rounding=ROUND_FLOOR, Emin=-2, Emax=2)
    hostile.traps[Inexact] = True
    with localcontext(hostile):
        assert actions[operation]() == expected
        assert getcontext().prec == 2 and getcontext().Emax == 2
        assert getcontext().traps[Inexact] is True
