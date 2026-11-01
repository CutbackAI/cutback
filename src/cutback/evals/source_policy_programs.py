"""Freeze a common policy search's complete native/cache envelope before approval.

This compiler performs no inference, fitting, content persistence or source edits.
It never observes test outcomes or shrinks confirmation evidence to fit a budget.
"""

from collections import Counter
from decimal import ROUND_DOWN, Context, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_serializer, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_runtime_reference import PolicyRuntimeReference
from cutback.core.request_profiles import fingerprint
from cutback.evals.campaigns import CampaignBudget, NativeGeneratorSpec
from cutback.evals.experiments import ExperimentPlan, provider_models, upper_cost
from cutback.evals.learned_routing import RoutingFitOptions
from cutback.evals.policy_models import checked_policies, policy_descriptors, policy_upper_costs
from cutback.pricing import Catalog


class SourcePolicyProgram(StrictModel):
    version: Literal["cutback.source-policy-program.v1", "cutback.source-policy-program.v2"] = (
        "cutback.source-policy-program.v1"
    )
    dataset_id: UUID
    baseline_snapshot_id: UUID
    environment: Identifier
    template: ExperimentPlan
    generator: NativeGeneratorSpec | None = None
    fit: RoutingFitOptions | None = Field(default_factory=RoutingFitOptions)
    budget: CampaignBudget
    conservative_savings_per_success_usd: Decimal | None = Field(
        default=None, ge=0, max_digits=24, decimal_places=12
    )
    forecast_successful_tasks: Annotated[StrictInt, Field(ge=1, le=1000000000)] | None = None
    max_fraction_of_opportunity: Decimal = Field(default=Decimal("0.1"), gt=0, le=Decimal("0.25"))
    runtime_reference: PolicyRuntimeReference | None = None

    @model_serializer(mode="wrap")
    def preserve_v1_wire(self, handler):
        value = handler(self)
        if self.runtime_reference is None:
            value.pop("runtime_reference", None)
        return value

    @field_validator(
        "conservative_savings_per_success_usd", "max_fraction_of_opportunity", mode="before"
    )
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_PROGRAM_EXACT_DECIMAL_MONEY_REQUIRED")
        return value

    @model_validator(mode="after")
    def qualified(self):
        plan = self.template
        execution = plan.source_policy_execution
        if (
            not self.dataset_id.int
            or not self.baseline_snapshot_id.int
            or execution is None
            or execution.phase != "evaluation"
            or plan.split != "validation"
            or plan.predecessor_run_id
            or self.generator is not None
            and self.generator.transport != execution.transport
        ):
            raise ValueError("POLICY_PROGRAM_DISTINCT_CURRENT_REFERENCE_TEMPLATE_REQUIRED")
        if self.version == "cutback.source-policy-program.v1":
            if (
                self.runtime_reference is not None
                or execution.version != "cutback.source-policy-execution.v1"
                or execution.reference.cache_enabled
            ):
                raise ValueError("POLICY_PROGRAM_DISTINCT_CURRENT_REFERENCE_TEMPLATE_REQUIRED")
        else:
            runtime = self.runtime_reference
            if (
                runtime is None
                or execution.version != "cutback.source-policy-execution.v2"
                or execution.reference.cache_requested != runtime.cache_requested
                or not runtime.cache_requested
                and any(value.cache_enabled for value in execution.candidates)
                or runtime.source_binding_sha256
                != fingerprint(execution.source.model_dump(mode="json"))
                or runtime.summary.scope.environment != self.environment
                or runtime.summary.scope.policy_sha256 != execution.source.policy_sha256
                or runtime.summary.scope.source_commit != execution.source.commit
                or runtime.summary.scope.workflow != execution.source.workflow
            ):
                raise ValueError("POLICY_PROGRAM_CURRENT_RUNTIME_REFERENCE_REQUIRED")
        if self.generator is not None and (
            not plan.baseline.routes
            and self.generator.max_output_tokens > plan.baseline.max_output_tokens
            or self.generator.model.exact_cache is not None
        ):
            raise ValueError("POLICY_PROGRAM_GENERATOR_OUTSIDE_SOURCE_OUTPUT_FRAME")
        if any(
            value.key.startswith(("policy-generated-", "policy-fit-"))
            for value in [plan.baseline, *plan.candidates]
        ):
            raise ValueError("POLICY_PROGRAM_STATIC_KEY_USES_DERIVATION_NAMESPACE")
        return self


def _dataset(specification, dataset, policies):
    plan = specification.template
    execution = plan.source_policy_execution
    if execution is None or dataset["immutable_hash"] != plan.dataset_hash:
        raise ValueError("POLICY_PROGRAM_FROZEN_SOURCE_DATASET_REQUIRED")
    if plan.price_sha256 != Catalog().sha256:
        raise ValueError("POLICY_PROGRAM_PRICE_CONTRACT_CHANGED")
    if not 1 <= len(dataset["items"]) <= 1024:
        raise ValueError("POLICY_PROGRAM_DATASET_CASE_BOUND")
    counts = {key: 0 for key in ("train", "validation", "test")}
    groups: dict[str, str] = {}
    validation_inputs, used = [], 0
    for item in dataset["items"]:
        split, case = item["split"], item["case"]
        if split not in counts or not isinstance(item.get("group_hash"), str):
            raise ValueError("POLICY_PROGRAM_COMPLETE_SPLIT_LINEAGE_REQUIRED")
        if item["group_hash"] in groups and groups[item["group_hash"]] != split:
            raise ValueError("POLICY_PROGRAM_FAMILY_SPANS_DATASET_SPLITS")
        groups[item["group_hash"]] = split
        text, label = case.get("input"), case.get("gold")
        if (
            type(text) is not str
            or not text
            or len(text) > execution.source.max_input_bytes
            or type(label) is not str
            or not label
            or len(label) > 1024
            or case.get("episode")
        ):
            raise ValueError("POLICY_PROGRAM_BOUNDED_LABELED_SOURCE_TEXT_REQUIRED")
        try:
            used += len(text.encode()) + len(label.encode())
            if len(label.encode()) > 1024:
                raise ValueError
            for policy in policies.values():
                target = policy.target(text)
                if len((target.instruction + text).encode()) + 4096 > target.max_input_tokens:
                    raise ValueError
        except (ValueError, UnicodeError):
            raise ValueError("POLICY_PROGRAM_SOURCE_INPUT_OR_NATIVE_BOUND_EXCEEDED") from None
        if used > 16 * 1048576:
            raise ValueError("POLICY_PROGRAM_DATASET_CONTENT_BOUND")
        counts[split] += 1
        if split == "validation":
            validation_inputs.append(text)
    if (
        not counts["validation"]
        or counts["test"] < plan.metric.min_test_samples
        or any(count > execution.max_cases for count in counts.values())
        or specification.fit is not None
        and not 2 <= counts["train"] <= 256
        or specification.generator is not None
        and not counts["train"]
    ):
        raise ValueError("POLICY_PROGRAM_COMPLETE_UNREDUCED_EVIDENCE_REQUIRED")
    return counts, validation_inputs


def compile_policy_program(
    specification: SourcePolicyProgram,
    dataset,
    policies,
    baseline_cost,
    *,
    independent_labels=False,
):
    """Bound every conditional path under one cap; choose controls before approval.

    Exact duplicate inputs establish cache trial eligibility only for this frozen
    validation corpus. They are never a measured production hit rate or a quality
    claim. Current server rights and workload evidence need separate rechecks.
    """
    if baseline_cost is not None and (
        not isinstance(baseline_cost, Decimal) or not baseline_cost.is_finite() or baseline_cost < 0
    ):
        raise ValueError("POLICY_PROGRAM_BASELINE_COST_REQUIRES_FINITE_DECIMAL")
    plan = specification.template
    execution = plan.source_policy_execution
    if execution is None:
        raise ValueError("POLICY_PROGRAM_SOURCE_EXECUTION_REQUIRED")
    references = checked_policies(execution, policies)
    descriptions = policy_descriptors(execution, policies)
    if any(descriptions[value.key] != value for value in [plan.baseline, *plan.candidates]):
        raise ValueError("POLICY_PROGRAM_FROZEN_DESCRIPTORS_CHANGED")
    original = policies[execution.reference.key]
    if (
        specification.version == "cutback.source-policy-program.v1"
        and original.cache_execution is not None
    ):
        raise ValueError("POLICY_PROGRAM_CURRENT_UNCACHED_SOURCE_FRAME_REQUIRED")
    if specification.version == "cutback.source-policy-program.v2":
        if original.cache_execution is None or specification.runtime_reference is None:
            raise ValueError("POLICY_PROGRAM_CURRENT_CACHED_SOURCE_FRAME_REQUIRED")
        specification.runtime_reference.require_current(execution.source)
    if specification.generator is not None and (
        specification.generator.max_instruction_bytes > original.max_instruction_bytes
        or specification.generator.max_output_tokens > original.baseline.max_output_tokens
    ):
        raise ValueError("POLICY_PROGRAM_GENERATOR_INSTRUCTION_BOUND_CHANGED")
    counts, inputs = _dataset(specification, dataset, policies)
    costs = policy_upper_costs(execution, policies)
    baseline = costs[execution.reference.key]
    native_baseline = max(upper_cost(model) for model in provider_models(original.strategy))
    if specification.generator is not None:
        from cutback.evals.policy_generation import seeds

        for seed in seeds(policies, specification=specification).values():
            possible = seed.strategy.model_copy(
                update={"max_output_tokens": specification.generator.max_output_tokens}
            )
            if upper_cost(possible) > native_baseline:
                raise ValueError("POLICY_PROGRAM_GENERATOR_NATIVE_UPPER_EXCEEDS_CURRENT_REFERENCE")
    multiplicities = Counter(inputs)
    hits = sum(number - 1 for number in multiplicities.values())
    eligible, omitted, cache_evidence = [], {}, {}
    for reference in references[1:]:
        policy = policies[reference.key]
        if any(upper_cost(model) > native_baseline for model in provider_models(policy.strategy)):
            raise ValueError("POLICY_PROGRAM_NATIVE_TARGET_EXCEEDS_REFERENCE_BOUND")
        if reference.cache_enabled:
            cache_evidence[reference.key] = {
                "scope": "frozen_validation_corpus_not_production",
                "requests": len(inputs),
                "potential_exact_hits": hits,
                "production_hit_rate": None,
                "realized_savings_usd": None,
            }
            if not hits:
                omitted[reference.key] = "no_repeated_frozen_validation_inputs"
                continue
        # A cache miss may cost slightly more than the uncached reference. The
        # whole envelope reserves that overhead; only measured trial receipts
        # can establish net benefit across hits and misses.
        if not reference.cache_enabled and costs[reference.key] > baseline:
            omitted[reference.key] = "declared_complete_call_upper_exceeds_reference"
            continue
        eligible.append(reference.key)
    reasons = []
    if (
        specification.version == "cutback.source-policy-program.v2"
        and execution.reference.cache_enabled
    ):
        test_inputs = [
            item["case"]["input"] for item in dataset["items"] if item["split"] == "test"
        ]
        if not hits:
            reasons.append("current_cache_replay_requires_repeated_validation_inputs")
        if len(test_inputs) == len(set(test_inputs)):
            reasons.append("current_cache_replay_requires_repeated_confirmation_inputs")
    if specification.generator is not None:
        from cutback.evals.policy_generation import input_upper_bytes

        if (
            input_upper_bytes(specification, dataset, policies)
            > specification.generator.model.max_input_tokens
        ):
            reasons.append("complete_generation_input_exceeds_approved_bound")
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
    if not eligible:
        reasons.append("no_eligible_static_policy_controls")
    with localcontext(Context(prec=80)):
        gross = savings * volume if savings is not None and volume is not None else None
        cap = (
            min(
                specification.budget.max_usd, gross * specification.max_fraction_of_opportunity
            ).quantize(Decimal("0.000000000001"), rounding=ROUND_DOWN)
            if gross is not None
            else None
        )
        phases, requests, chosen = None, None, []
        # Controls may be omitted before approval. No split, case or criterion
        # changes after approval, including the full untouched confirmation.
        for count in range(len(eligible), 0, -1):
            selected = eligible[:count]
            cached_current = specification.version == "cutback.source-policy-program.v2"
            generator = specification.generator
            generated = generator.rounds * generator.variants_per_round if generator else 0
            native_controls = sum(
                not policies[key].strategy.routes
                and (cached_current or not policies[key].cache_execution)
                for key in selected
            )
            training_controls = min(4, native_controls + generated)
            fit_enabled = specification.fit is not None and training_controls > 0
            cache_bounds = [
                policies[key].cache_execution.operation_cost_upper_usd
                for key in selected
                if policies[key].cache_execution is not None
                and next(value for value in references if value.key == key).cache_enabled
            ]
            if (
                specification.version == "cutback.source-policy-program.v2"
                and execution.reference.cache_enabled
            ):
                cache_bounds.append(original.cache_execution.operation_cost_upper_usd)
            routed_count = (1 if cached_current else 1 + bool(cache_bounds)) if fit_enabled else 0
            cache_upper = max(cache_bounds, default=Decimal(0)) * 2
            next_phases = {
                "static_validation": counts["validation"]
                * (baseline + sum((costs[key] for key in selected), Decimal(0))),
                "generation_if_controls_fail": generator.rounds * upper_cost(generator.model)
                if generator
                else Decimal(0),
                "generated_validation_if_controls_fail": counts["validation"]
                * (generator.rounds + generated)
                * baseline
                if generator
                else Decimal(0),
                "training_if_search_fails": counts["train"]
                * (1 + training_controls)
                * native_baseline
                if fit_enabled
                else Decimal(0),
                "routed_validation_if_search_fails": counts["validation"]
                * ((1 + routed_count) * baseline + cache_upper)
                if fit_enabled
                else Decimal(0),
                "untouched_confirmation": counts["test"] * (2 * baseline + cache_upper),
            }
            number = (
                counts["validation"] * (1 + count)
                + (generator.rounds if generator else 0)
                + counts["validation"] * ((generator.rounds if generator else 0) + generated)
                + (
                    counts["train"] * (1 + training_controls)
                    + counts["validation"] * (1 + routed_count)
                    if fit_enabled
                    else 0
                )
                + counts["test"] * 2
            )
            if (
                not reasons
                and cap is not None
                and cap > 0
                and sum(next_phases.values(), Decimal(0)) <= cap
                and number <= specification.budget.max_requests
            ):
                phases, requests, chosen = next_phases, number, selected
                break
        if not chosen and not reasons:
            reasons.append("complete_program_exceeds_opportunity_or_request_cap")
        result = {
            "contract": "cutback.source-policy-program-budget.v1",
            "eligible": bool(chosen),
            "independent_labels_declared": independent_labels is True,
            "selected_control_keys": chosen,
            "omitted_controls": {
                **omitted,
                **{key: "whole_program_budget" for key in eligible if key not in chosen},
            },
            "effective_max_usd": str(cap) if cap is not None else None,
            "max_requests": requests,
            "worst_case_native_and_cache_usd": str(sum(phases.values(), Decimal(0)))
            if phases
            else None,
            "phase_upper_usd": {key: str(value) for key, value in phases.items()}
            if phases
            else None,
            "dataset_split_counts": counts,
            "cache_trial_eligibility": cache_evidence,
            "reference_kind": "current_source_router"
            if original.strategy.routes
            else "current_source_native",
            "historical_anchor_substituted_for_reference": False,
            "baseline_complete_declared_cost_per_success_usd": str(baseline_cost)
            if baseline_cost is not None
            else None,
            "forecast_gross_modeled_opportunity_usd": str(gross) if gross is not None else None,
            "strategy": "simple_then_instruction_search_then_conditional_native_routing",
            "confirmation_sample_reduction": False,
            "fit_compute_usd": None,
            "routing_hosting_usd": None,
            "full_task_cost_advantage": None,
            "execution_authorized": False,
            "production_changed": False,
            "reasons": reasons,
        }
        if specification.version == "cutback.source-policy-program.v2":
            runtime = specification.runtime_reference
            if runtime is None:
                raise ValueError("POLICY_PROGRAM_CURRENT_RUNTIME_REFERENCE_REQUIRED")
            result.update(
                contract="cutback.source-policy-program-budget.v2",
                reference_runtime_cache_requested=execution.reference.cache_requested,
                reference_runtime_evidence_sha256=runtime.summary_sha256,
                training_reference_kind="uncached_shadow_of_current_source",
                runtime_identity_basis="client_reported_source_map",
                reference_execution_scope="isolated_cold_cache_replay"
                if execution.reference.cache_enabled
                else "uncached_source_policy_replay",
            )
        return result


def policy_confirmation_gate(report, plan: ExperimentPlan, *, sample_only=False):
    """Separate source-policy contract; does not widen the ordinary source-edit gate."""
    from cutback.evals.datasets import digest

    reasons = []
    execution = plan.source_policy_execution
    if (
        execution is None
        or execution.phase != "evaluation"
        or plan.split != "test"
        or not plan.predecessor_run_id
        or len(plan.candidates) != 1
        or plan.evaluator_version != "deterministic-v19"
    ):
        reasons.append("distinct_frozen_policy_confirmation_required")
    expected = digest(plan.model_dump(mode="json"))
    if report.get("plan", {}).get("plan_hash") != expected:
        reasons.append("frozen_plan_mismatch")
    if execution is not None and any(
        report.get("source_policy_execution", {}).get(key) != value
        for key, value in execution.public_summary().items()
    ):
        reasons.append("source_policy_receipt_binding_changed")
    samples = report.get("sample_n")
    if (
        type(samples) is not int
        or samples < plan.metric.min_test_samples
        or report.get("dataset_hash") != plan.dataset_hash
    ):
        reasons.append("complete_frozen_test_samples_required")
    requests = report.get("requests")
    if (
        report.get("status") != "COMPLETED"
        or report.get("billing_complete") is not True
        or type(requests) is not int
        or requests < 1
        or report.get("known_cost_requests") != requests
    ):
        reasons.append("canonical_accounting_incomplete")
    if not sample_only and report.get("synthetic_execution"):
        reasons.append("fixture_transport_cannot_verify_real_model_quality")
    if not sample_only and report.get("lineage", {}).get("fresh_test_claim") is not True:
        reasons.append("independent_test_lineage_unknown_or_exposed")
    selected = plan.candidates[0].key if len(plan.candidates) == 1 else None
    choices = [
        value
        for value in report.get("recommendations", [])
        if value.get("candidate", {}).get("key") == selected
        and value.get("evaluator_version") == "deterministic-v19"
        and value.get("quality_gate_passed") is True
        and value.get("cheaper_on_sample") is True
        and (
            sample_only
            or value.get("evidence_tier") == 4
            and value.get("statistical_gate_passed") is True
            and value.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        )
    ]
    if len(choices) != 1:
        reasons.append("selected_policy_cost_and_quality_evidence_insufficient")
    reported_models = report.get("models", [])
    models = {value.get("model", {}).get("key"): value for value in reported_models}
    if execution is not None:
        if (
            len(models) != len(reported_models)
            or any(
                value.get("cost_complete") is not True
                or value.get("requests") != samples
                or value.get("quality", {}).get("sample_n") != samples
                for value in models.values()
            )
            or set(models)
            != {execution.reference.key, *[value.key for value in execution.candidates]}
            or type(samples) is int
            and requests != samples * len(models)
        ):
            reasons.append("complete_paired_policy_receipts_required")
        if len(choices) == 1 and choices[0].get("paired_delta", {}).get("sample_n") != samples:
            reasons.append("complete_paired_policy_receipts_required")
        for reference in [execution.reference, *execution.candidates]:
            cache = models.get(reference.key, {}).get("exact_cache")
            if reference.cache_enabled and (
                not isinstance(cache, dict) or cache.get("execution_qualified") is not True
            ):
                reasons.append("selected_cache_execution_not_qualified")
    return {
        "contract": "cutback.source-policy-confirmation.v1",
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "scope": "sample_only" if sample_only else "independent_heldout_dataset",
        "candidate_keys": [selected] if not reasons else [],
        "plan_hash": expected,
        "production_deployed": False,
        "production_savings_usd": None,
        "full_task_cost_advantage": None,
    }
