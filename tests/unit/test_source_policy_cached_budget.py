"""The current cached reference and its uncached training have different complete bounds."""

from decimal import Decimal
from uuid import uuid4

import pytest
from test_policy_runtime_reference import frame
from test_source_policy_program_budget import program

from cutback.core.policy_execution import SourcePolicyExecutionSpec, uncached_shadow_reference
from cutback.core.policy_runtime_reference import freeze_reference
from cutback.evals.experiments import ExperimentPlan, upper_cost
from cutback.evals.policy_artifacts import prepare_policy_payload
from cutback.evals.policy_models import policy_descriptors
from cutback.evals.source_policy_programs import SourcePolicyProgram, compile_policy_program


def cached_program(*, requested=True, cheap_current=False, cached_controls=False, generate=False):
    old, data, policies = program(cached=False, generate=generate)
    cached, _, summary, now = frame(active=requested)
    raw = policies["reference"].model_dump(mode="json")
    raw["cache_execution"] = cached.cache_execution.model_dump(mode="json")
    from cutback.core.improvement_policy import SourceImprovementPolicy

    if cheap_current:
        raw["strategy"] = policies["cheap"].strategy.model_dump(mode="json")
        stronger = policies["reference"].model_copy(deep=True)
        policies["cheap"] = stronger
    policies["reference"] = SourceImprovementPolicy.model_validate(raw)
    if cached_controls:
        policies["cheap"] = policies["cheap"].model_copy(
            update={"cache_execution": policies["reference"].cache_execution}
        )
    source = old.template.source_policy_execution.source.model_copy(
        update={"policy_sha256": policies["reference"].policy_sha256}
    )
    references = [prepare_policy_payload(key, uuid4(), value)[1] for key, value in policies.items()]
    references = [
        (
            value.model_copy(update={"cache_requested": True})
            if requested
            else uncached_shadow_reference(value)
        )
        if value.cache_enabled
        else value
        for value in references
    ]
    execution = SourcePolicyExecutionSpec(
        version="cutback.source-policy-execution.v2",
        source=source,
        reference=references[0],
        candidates=references[1:],
        transport="scripted_test_only",
    )
    models = policy_descriptors(execution, policies)
    plan = ExperimentPlan.model_validate(
        {
            **old.template.model_dump(mode="json"),
            "baseline": models["reference"].model_dump(mode="json"),
            "candidates": [models["cheap"].model_dump(mode="json")],
            "source_policy_execution": execution.model_dump(mode="json"),
        }
    )
    summary = summary.model_copy(
        update={
            "scope": summary.scope.model_copy(
                update={"policy_sha256": source.policy_sha256, "workflow": source.workflow}
            )
        }
    )
    runtime = freeze_reference(source, summary, now=now)
    specification = SourcePolicyProgram.model_validate(
        {
            **old.model_dump(mode="json"),
            "version": "cutback.source-policy-program.v2",
            "template": plan.model_dump(mode="json"),
            "runtime_reference": runtime.model_dump(mode="json"),
        }
    )
    return specification, data, policies


@pytest.mark.parametrize("requested", [False, True])
def test_compiled_reference_preserves_actual_activation_and_never_discounts_the_native_worst_case(
    requested,
):
    specification, data, policies = cached_program(requested=requested)
    result = compile_policy_program(
        specification, data, policies, Decimal("1"), independent_labels=True
    )
    assert result["eligible"] and result["contract"] == "cutback.source-policy-program-budget.v2"
    assert result["reference_runtime_cache_requested"] is requested
    assert result["training_reference_kind"] == "uncached_shadow_of_current_source"
    assert (
        result["reference_runtime_evidence_sha256"]
        == specification.runtime_reference.summary_sha256
    )
    assert result["full_task_cost_advantage"] is None and not result["execution_authorized"]
    assert policies["reference"].cache_execution is not None


def test_training_reserves_uncached_native_calls_even_when_current_comparison_uses_cache():
    active, data, policies = cached_program(requested=True)
    inactive, other_data, other_policies = cached_program(requested=False)
    first = compile_policy_program(active, data, policies, Decimal("1"), independent_labels=True)
    second = compile_policy_program(
        inactive, other_data, other_policies, Decimal("1"), independent_labels=True
    )
    assert (
        first["phase_upper_usd"]["training_if_search_fails"]
        == second["phase_upper_usd"]["training_if_search_fails"]
    )
    assert Decimal(first["phase_upper_usd"]["training_if_search_fails"]) == 8 * upper_cost(
        policies["reference"].strategy
    )
    assert Decimal(first["phase_upper_usd"]["static_validation"]) - Decimal(
        second["phase_upper_usd"]["static_validation"]
    ) == Decimal("0.000008")


def test_legacy_program_cannot_accept_new_runtime_semantics_or_lose_the_current_source_binding():
    specification, _, _ = cached_program()
    raw = specification.model_dump(mode="json")
    with pytest.raises(ValueError):
        SourcePolicyProgram.model_validate({**raw, "version": "cutback.source-policy-program.v1"})
    with pytest.raises(ValueError):
        SourcePolicyProgram.model_validate({**raw, "runtime_reference": None})
    raw["runtime_reference"]["source_binding_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        SourcePolicyProgram.model_validate(raw)


def test_cache_overhead_does_not_allow_a_more_expensive_native_target():
    specification, data, policies = cached_program(cheap_current=True)
    with pytest.raises(ValueError, match="NATIVE_TARGET_EXCEEDS_REFERENCE_BOUND"):
        compile_policy_program(specification, data, policies, Decimal("1"), independent_labels=True)


def test_incumbent_cache_cannot_be_omitted_when_frozen_evidence_cannot_exercise_a_hit():
    specification, data, policies = cached_program(requested=True)
    for index, item in enumerate(data["items"]):
        item["case"]["input"] = f"owned-unique-{item['split']}-{index}"
    result = compile_policy_program(
        specification, data, policies, Decimal("1"), independent_labels=True
    )
    assert not result["eligible"]
    assert set(result["reasons"]) >= {
        "current_cache_replay_requires_repeated_validation_inputs",
        "current_cache_replay_requires_repeated_confirmation_inputs",
    }
    assert result["historical_anchor_substituted_for_reference"] is False
