"""Freeze source-routing effort before approval; no SDK, network or app execution.

A simple native control is evaluated first. Only a failed control permits the
reserved train/fit/routed-validation path. Either path retains a full untouched
confirmation allocation. These are native-call bounds, not complete task costs.
"""

from decimal import ROUND_DOWN, Context, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from cutback.core.input_routing import (
    SOURCE_DATASET_CASE_BOUND,
    SOURCE_DATASET_CONTENT_BOUND,
    LearnedRoutingBinding,
    RoutingTrainingExecution,
)
from cutback.core.models import Identifier, StrictModel
from cutback.evals.campaigns import CampaignBudget
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec, upper_cost
from cutback.evals.learned_routing import RoutingFitOptions
from cutback.pricing import Catalog


class RoutingProgram(StrictModel):
    version: Literal["cutback.routing-improvement-program.v1"] = (
        "cutback.routing-improvement-program.v1"
    )
    dataset_id: UUID
    baseline_snapshot_id: UUID
    environment: Identifier
    template: ExperimentPlan
    fit: RoutingFitOptions = Field(default_factory=RoutingFitOptions)
    budget: CampaignBudget
    conservative_savings_per_success_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=24, decimal_places=12
    )
    forecast_successful_tasks: Annotated[StrictInt, Field(ge=1, le=1000000000)] | None = None
    max_fraction_of_opportunity: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.25"))

    @model_validator(mode="after")
    def qualified(self):
        plan = self.template
        if (
            not self.dataset_id.int
            or not self.baseline_snapshot_id.int
            or not isinstance(plan.routing_execution, RoutingTrainingExecution)
            or plan.routing_execution.source is None
            or plan.evaluator_version != "deterministic-v16"
            or plan.split != "train"
            or plan.approved_providers != ["openai"]
            or plan.metric.kind != "exact_label"
        ):
            raise ValueError("ROUTING_PROGRAM_REQUIRES_SOURCE_BOUND_TRAINING_TEMPLATE")
        models = [plan.baseline, *plan.candidates]
        if len(plan.candidates) > 4 or len({model.key for model in models}) != len(models):
            raise ValueError("ROUTING_PROGRAM_REQUIRES_ONE_TO_FOUR_DISTINCT_CONTROLS")
        identities = [digest(model.model_dump(mode="json", exclude={"key"})) for model in models]
        if len(set(identities)) != len(identities):
            raise ValueError("ROUTING_PROGRAM_CONTROL_CONFIGURATION_DUPLICATED")
        if any(upper_cost(model) >= upper_cost(plan.baseline) for model in plan.candidates):
            raise ValueError("ROUTING_PROGRAM_REQUIRES_CHEAPER_NATIVE_CONTROLS")
        return self


def selected_controls(specification: RoutingProgram, envelope) -> list[ModelSpec]:
    keys = envelope.get("selected_control_keys")
    if (
        envelope.get("contract") != "cutback.routing-program-native-budget.v1"
        or envelope.get("eligible") is not True
        or not isinstance(keys, list)
        or not keys
        or keys != [model.key for model in specification.template.candidates[: len(keys)]]
    ):
        raise ValueError("ROUTING_PROGRAM_SELECTED_CONTROLS_CHANGED")
    return specification.template.candidates[: len(keys)]


def compile_routing_program(
    specification: RoutingProgram, dataset, baseline_cost, *, independent_labels=False
):
    """Reserve all worst-case paths; narrow controls, never evidence or criteria.

    baseline_cost must be the canonical complete-under-declaration task snapshot.
    The owner supplies opportunity/volume assumptions; they are not observations
    of future benefit. Fitting/hosting/engineering USD remain unknown here.
    """
    if baseline_cost is not None and (
        not isinstance(baseline_cost, Decimal) or not baseline_cost.is_finite() or baseline_cost < 0
    ):
        raise ValueError("ROUTING_PROGRAM_BASELINE_COST_MUST_BE_FINITE_DECIMAL")
    plan = specification.template
    if dataset["immutable_hash"] != plan.dataset_hash or plan.price_sha256 != Catalog().sha256:
        raise ValueError("ROUTING_PROGRAM_DATASET_OR_PRICE_CONTRACT_CHANGED")
    if not 1 <= len(dataset["items"]) <= SOURCE_DATASET_CASE_BOUND:
        raise ValueError("ROUTING_PROGRAM_DATASET_CASE_BOUND")
    execution = plan.routing_execution
    if not isinstance(execution, RoutingTrainingExecution) or execution.source is None:
        raise ValueError("ROUTING_PROGRAM_SOURCE_CONTRACT_REQUIRED")
    counts = {split: 0 for split in ("train", "validation", "test")}
    content_bytes = 0
    for item in dataset["items"]:
        split = item["split"]
        if split not in counts:
            raise ValueError("ROUTING_PROGRAM_DATASET_SPLIT_UNSUPPORTED")
        case = item["case"]
        text = case.get("input")
        if (
            not isinstance(text, str)
            or not text
            or not isinstance(case.get("gold"), str)
            or not case["gold"]
            or case.get("episode") is not None
            or len(text.encode()) > execution.source.max_input_bytes
        ):
            raise ValueError("ROUTING_PROGRAM_REQUIRES_BOUNDED_LABELED_SOURCE_TEXT")
        if any(
            len((model.instruction + text).encode()) + 4096 > model.max_input_tokens
            for model in [plan.baseline, *plan.candidates]
        ):
            raise ValueError("ROUTING_PROGRAM_INPUT_EXCEEDS_DECLARED_NATIVE_BOUND")
        content_bytes += len(text.encode()) + len(case["gold"].encode())
        if content_bytes > SOURCE_DATASET_CONTENT_BOUND:
            raise ValueError("ROUTING_PROGRAM_DATASET_CONTENT_BOUND")
        counts[split] += 1
    if (
        not 2 <= counts["train"] <= execution.max_cases
        or not counts["validation"]
        or counts["test"] < plan.metric.min_test_samples
    ):
        raise ValueError("ROUTING_PROGRAM_REQUIRES_COMPLETE_TRAIN_VALIDATION_AND_TEST")
    reasons = []
    if independent_labels is not True:
        reasons.append("independent_labels_not_declared")
    savings, volume = (
        specification.conservative_savings_per_success_usd,
        specification.forecast_successful_tasks,
    )
    if baseline_cost is None or savings is None or volume is None:
        reasons.append("complete_baseline_cost_or_conservative_opportunity_unknown")
    elif savings > baseline_cost:
        reasons.append("unit_savings_assumption_exceeds_observed_baseline_cost")
    with localcontext(Context(prec=80)):
        gross = savings * volume if savings is not None and volume is not None else None
        cap = (
            min(
                specification.budget.max_usd, gross * specification.max_fraction_of_opportunity
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if gross is not None
            else None
        )
        base = upper_cost(plan.baseline)
        chosen, requested, costs, requests = [], plan.candidates, None, 0
        for count in range(len(requested), 0, -1):
            controls = requested[:count]
            paired = base + sum((upper_cost(model) for model in controls), Decimal(0))
            steps = {
                "static_validation": counts["validation"] * paired,
                "training_if_controls_fail": counts["train"] * paired,
                "routed_validation_if_controls_fail": counts["validation"] * 2 * base,
                "untouched_confirmation": counts["test"] * 2 * base,
            }
            total = sum(steps.values(), Decimal(0))
            number = (counts["validation"] + counts["train"]) * (1 + len(controls)) + (
                counts["validation"] + counts["test"]
            ) * 2
            if (
                not reasons
                and gross is not None
                and gross > 0
                and cap is not None
                and total <= cap
                and number <= specification.budget.max_requests
            ):
                chosen, costs, requests = controls, steps, number
                break
        if not chosen and not reasons:
            reasons.append("complete_program_exceeds_opportunity_or_request_cap")
        return {
            "contract": "cutback.routing-program-native-budget.v1",
            "eligible": bool(chosen),
            "independent_labels_declared": independent_labels is True,
            "requested_control_count": len(requested),
            "selected_control_keys": [model.key for model in chosen],
            "omitted_control_keys": [model.key for model in requested[len(chosen) :]],
            "max_requests": requests,
            "effective_max_usd": str(cap) if cap is not None else None,
            "worst_case_native_usd": str(sum(costs.values(), Decimal(0))) if costs else None,
            "native_phase_upper_usd": {key: str(value) for key, value in costs.items()}
            if costs
            else None,
            "dataset_split_counts": counts,
            "forecast_gross_modeled_opportunity_usd": str(gross) if gross is not None else None,
            "baseline_complete_declared_cost_per_success_usd": str(baseline_cost)
            if baseline_cost is not None
            else None,
            "fit_compute_usd": None,
            "routing_hosting_usd": None,
            "full_task_cost_advantage": None,
            "strategy": "native_controls_first_fit_only_if_controls_fail",
            "confirmation_sample_reduction": False,
            "production_changed": False,
            "reasons": reasons,
        }


def program_static_plan(specification: RoutingProgram, controls, *, nonce: UUID, budget: Budget):
    """The qualified wrapper sends the same ordinary text/native model frame.

    This is an unauthorised child draft. Only the program's authenticated parent
    may consume it under that envelope; it inherits no standalone paid approval.
    """
    raw = specification.template.model_dump(mode="json")
    raw.update(
        name="routing-program-native-controls",
        split="validation",
        candidates=[model.model_dump(mode="json") for model in controls],
        routing_execution=None,
        evaluator_version="deterministic-v3",
        content_nonce=str(nonce),
        budget=budget.model_dump(mode="json"),
    )
    return ExperimentPlan.model_validate(raw)


def program_confirmation_gate(report, plan: ExperimentPlan, *, sample_only=False):
    """A routing receipt cannot authorize an ordinary model/source-edit workflow.

    Keep that existing gate unchanged. This gate is confined to a frozen source
    routing confirmation and requires its authenticated fit and untouched lineage.
    Server callers must still refresh content/fit/selection authority.
    """
    from cutback.evals.search import regression_gate

    expected_hash = digest(plan.model_dump(mode="json"))
    if plan.routing_execution is None:
        return regression_gate(
            report,
            expected_plan_hash=expected_hash,
            candidate=plan.candidates[0].key,
            sample_only=sample_only,
        )
    reasons = []
    binding = plan.routing_execution
    if (
        plan.evaluator_version != "deterministic-v16"
        or not isinstance(binding, LearnedRoutingBinding)
        or binding.source is None
        or plan.split != "test"
        or not plan.predecessor_run_id
        or len(plan.candidates) != 1
    ):
        reasons.append("source_bound_routing_confirmation_required")
    if report.get("status") != "COMPLETED" or report.get("billing_complete") is not True:
        reasons.append("run_or_accounting_incomplete")
    if report.get("plan", {}).get("plan_hash") != expected_hash:
        reasons.append("frozen_plan_mismatch")
    if report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    if report.get("routing_source_current") is not True or report.get(
        "routing_fit_binding"
    ) != binding.model_dump(mode="json"):
        reasons.append("current_authenticated_routing_fit_required")
    if (report.get("native_fixture") or report.get("synthetic_execution")) and not sample_only:
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    choices = [
        item
        for item in report.get("recommendations", [])
        if item.get("candidate", {}).get("key") == plan.candidates[0].key
        and item.get("evaluator_version") == "deterministic-v16"
        and item.get("quality_gate_passed") is True
        and item.get("cheaper_on_sample") is True
    ]
    if not sample_only:
        choices = [
            item
            for item in choices
            if item.get("evidence_tier") == 4
            and item.get("statistical_gate_passed") is True
            and item.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        ]
    if not choices:
        reasons.append("no_candidate_meets_required_evidence_cost_and_quality")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": [plan.candidates[0].key] if not reasons else [],
        "plan_hash": expected_hash,
        "production_deployed": False,
        "production_savings_usd": None,
    }
