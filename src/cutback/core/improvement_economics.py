"""Descriptive task-window comparisons and conservative, declared search effort."""

from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from cutback.core.models import StrictModel
from cutback.core.task_outcomes import ComponentMoney, TaskCostComponent, TaskEconomicsSummary


class OptimizationRunCost(StrictModel):
    run_id: UUID
    status: str
    requests_reserved: Annotated[StrictInt, Field(ge=0, le=10000)]
    known_cost_requests: Annotated[StrictInt, Field(ge=0, le=10000)] | None
    known_cost_subtotal_usd: ComponentMoney
    ledger_version: StrictInt | None
    uncertain_or_reserved_calls: Annotated[StrictInt, Field(ge=0, le=10000)]

    @model_validator(mode="after")
    def valid(self):
        if (
            self.known_cost_requests is not None
            and self.known_cost_requests > self.requests_reserved
        ):
            raise ValueError("OPTIMIZATION_KNOWN_COST_COUNT_EXCEEDS_REQUESTS")
        return self

    @property
    def cost_complete(self):
        return bool(
            self.ledger_version == 1
            and self.status in {"COMPLETED", "FAILED", "CANCELLED"}
            and self.known_cost_requests == self.requests_reserved
            and self.uncertain_or_reserved_calls == 0
        )


class ImprovementAssumptions(StrictModel):
    all_optimization_runs_declared_complete: StrictBool = False
    other_optimization_costs_declared_complete: StrictBool = False
    other_optimization_costs: list[TaskCostComponent] = Field(default_factory=list, max_length=32)
    min_tasks_per_window: Annotated[StrictInt, Field(ge=1, le=10000)] = 30
    max_absolute_success_rate_loss: Decimal = Field(default=Decimal(0), ge=0, le=1)
    forecast_successful_tasks: Annotated[StrictInt, Field(ge=1, le=1000000000)] | None = None

    @model_validator(mode="after")
    def unique(self):
        if len({c.key for c in self.other_optimization_costs}) != len(
            self.other_optimization_costs
        ):
            raise ValueError("OPTIMIZATION_COST_COMPONENT_KEYS_MUST_BE_UNIQUE")
        return self


def compare_improvement_economics(
    baseline: TaskEconomicsSummary,
    candidate: TaskEconomicsSummary,
    runs: list[OptimizationRunCost],
    assumptions: ImprovementAssumptions,
) -> dict:
    if len(runs) > 20 or len({r.run_id for r in runs}) != len(runs):
        raise ValueError("OPTIMIZATION_RUNS_MUST_BE_UNIQUE_AND_BOUNDED")
    reasons = []
    with localcontext() as context:
        context.prec = 64
        known_run_cost = sum((r.known_cost_subtotal_usd for r in runs), Decimal(0))
        known_other = sum(
            (
                c.amount_usd
                for c in assumptions.other_optimization_costs
                if c.amount_usd is not None
            ),
            Decimal(0),
        )
        overhead_complete = bool(
            assumptions.all_optimization_runs_declared_complete
            and all(r.cost_complete for r in runs)
            and assumptions.other_optimization_costs_declared_complete
            and all(c.amount_usd is not None for c in assumptions.other_optimization_costs)
        )
        overhead = known_run_cost + known_other if overhead_complete else None
        enough = (
            min(baseline.submitted_tasks, candidate.submitted_tasks)
            >= assumptions.min_tasks_per_window
        )
        outcomes_known = bool(
            baseline.submitted_tasks
            and candidate.submitted_tasks
            and not baseline.unknown_or_error_outcomes
            and not candidate.unknown_or_error_outcomes
        )
        baseline_rate = (
            Decimal(baseline.declared_successes) / baseline.submitted_tasks
            if outcomes_known
            else None
        )
        candidate_rate = (
            Decimal(candidate.declared_successes) / candidate.submitted_tasks
            if outcomes_known
            else None
        )
        outcome_gate = (
            candidate_rate >= baseline_rate - assumptions.max_absolute_success_rate_loss
            if baseline_rate is not None and candidate_rate is not None
            else None
        )
        costs_known = (
            baseline.window_cost_complete_under_declaration
            and candidate.window_cost_complete_under_declaration
            and baseline.declared_window_cost_per_success_usd is not None
            and candidate.declared_window_cost_per_success_usd is not None
        )
        delta = (
            (
                Decimal(baseline.declared_window_cost_per_success_usd)
                - Decimal(candidate.declared_window_cost_per_success_usd)
            )
            if costs_known
            and baseline.declared_window_cost_per_success_usd is not None
            and candidate.declared_window_cost_per_success_usd is not None
            else None
        )
        if not enough:
            reasons.append("insufficient_declared_task_sample")
        if outcome_gate is not True:
            reasons.append(
                "declared_outcome_regression"
                if outcome_gate is False
                else "outcome_coverage_unknown"
            )
        if delta is None:
            reasons.append("complete_window_cost_unknown")
        elif delta <= 0:
            reasons.append("no_observed_modeled_cost_improvement")
        if not overhead_complete:
            reasons.append("optimization_overhead_incomplete")
        if assumptions.forecast_successful_tasks is None:
            reasons.append("future_successful_task_volume_unknown")
        payback = (
            int((overhead / delta).to_integral_value(rounding=ROUND_CEILING))
            if overhead is not None and delta is not None and delta > 0
            else None
        )
        forecast_net = (
            Decimal(assumptions.forecast_successful_tasks) * delta - overhead
            if assumptions.forecast_successful_tasks is not None
            and delta is not None
            and overhead is not None
            else None
        )
        if forecast_net is not None and forecast_net <= 0:
            reasons.append("declared_horizon_does_not_repay_optimization_cost")
    return {
        "contract": "cutback.improvement-economics.v1",
        "decision": "prepare_independent_confirmation" if not reasons else "abstain_or_reject",
        "reasons": reasons,
        "baseline_declared_success_rate": str(baseline_rate) if baseline_rate is not None else None,
        "candidate_declared_success_rate": str(candidate_rate)
        if candidate_rate is not None
        else None,
        "descriptive_outcome_non_regression": outcome_gate,
        "modeled_cost_per_success_delta_usd": str(delta) if delta is not None else None,
        "known_optimization_run_subtotal_usd": str(known_run_cost)
        if any(
            (r.known_cost_requests or 0) > 0
            or r.known_cost_subtotal_usd > 0
            or (r.ledger_version == 1 and r.requests_reserved == 0)
            for r in runs
        )
        else None,
        "known_other_optimization_subtotal_usd": str(known_other)
        if any(c.amount_usd is not None for c in assumptions.other_optimization_costs)
        or (
            assumptions.other_optimization_costs_declared_complete
            and not assumptions.other_optimization_costs
        )
        else None,
        "optimization_cost_complete_under_declaration": overhead_complete,
        "declared_optimization_total_usd": str(overhead) if overhead is not None else None,
        "forecast_successful_tasks": assumptions.forecast_successful_tasks,
        "forecast_net_modeled_benefit_usd": str(forecast_net) if forecast_net is not None else None,
        "forecast_break_even_successful_tasks": str(payback) if payback is not None else None,
        "forecast_evidence": (
            "owner_declared_volume_and_descriptive_window_rates_not_measured_future_savings"
        ),
        "quality_evidence": "externally_asserted_descriptive_windows_not_independent_confirmation",
        "causal_attribution": "not_established",
        "production_savings_usd": None,
        "deployment_authorized": False,
    }


class SearchEffortInput(StrictModel):
    # A conservative opportunity assumption, not a price inferred from missing data.
    conservative_savings_per_success_usd: ComponentMoney | None = None
    forecast_successful_tasks: Annotated[StrictInt, Field(ge=1, le=1000000000)] | None = None
    per_candidate_upper_cost_usd: ComponentMoney | None = None
    fixed_proposer_upper_cost_usd: ComponentMoney | None = None
    owner_spend_cap_usd: ComponentMoney
    max_fraction_of_opportunity: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.25"))
    max_candidates: Annotated[StrictInt, Field(ge=1, le=5)] = 5
    evidence: Literal["owner_forecast_not_verified"] = "owner_forecast_not_verified"


def choose_search_effort(value: SearchEffortInput) -> dict:
    reasons = []
    if (
        value.conservative_savings_per_success_usd is None
        or value.forecast_successful_tasks is None
    ):
        reasons.append("opportunity_or_volume_unknown")
    if value.per_candidate_upper_cost_usd is None or value.fixed_proposer_upper_cost_usd is None:
        reasons.append("complete_search_upper_bound_unknown")
    gross = cap = None
    count = 0
    with localcontext() as context:
        context.prec = 64
        if (
            not reasons
            and value.conservative_savings_per_success_usd is not None
            and value.forecast_successful_tasks is not None
            and value.fixed_proposer_upper_cost_usd is not None
            and value.per_candidate_upper_cost_usd is not None
        ):
            gross = value.conservative_savings_per_success_usd * value.forecast_successful_tasks
            cap = min(
                value.owner_spend_cap_usd, gross * value.max_fraction_of_opportunity
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            remaining = cap - value.fixed_proposer_upper_cost_usd
            if gross <= 0 or remaining < 0:
                reasons.append("no_positive_qualified_search_budget")
            elif value.per_candidate_upper_cost_usd == 0:
                # Explicit known-zero compute is different from a missing bound.
                # The actual frozen plan must independently verify this forecast.
                count = value.max_candidates
            else:
                count = min(
                    value.max_candidates,
                    max(0, int(remaining // value.per_candidate_upper_cost_usd)),
                )
                if not count:
                    reasons.append("search_upper_bound_exceeds_conservative_opportunity")
    return {
        "decision": "prepare_frozen_search_plan" if count else "abstain",
        "candidate_limit": count,
        "forecast_gross_modeled_opportunity_usd": str(gross) if gross is not None else None,
        "suggested_total_spend_cap_usd": str(cap) if cap is not None else None,
        "reasons": reasons,
        "evidence": value.evidence,
        "paid_calls_authorized": False,
        "provider_calls": 0,
    }
