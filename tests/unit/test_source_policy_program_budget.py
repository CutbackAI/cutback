"""Whole search bounds never spend the reserved untouched confirmation allocation."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from test_source_improvement_policy import policy

from cutback.core.policy_execution import SourcePolicyExecutionSpec
from cutback.core.policy_producer import PolicySourceBinding
from cutback.evals.campaigns import NativeGeneratorSpec
from cutback.evals.experiments import ExperimentPlan, ModelSpec
from cutback.evals.policy_artifacts import prepare_policy_payload
from cutback.evals.policy_models import policy_descriptors
from cutback.evals.source_policy_programs import SourcePolicyProgram, compile_policy_program


def program(*, cached=True, requests=200, generate=True):
    original = policy()
    original.strategy = original.baseline
    cheap = policy()
    cached_policy = policy(routed=True, cached=True)
    policies = {"reference": original, "cheap": cheap}
    if cached:
        policies["routed-cache"] = cached_policy
    references = [prepare_policy_payload(key, uuid4(), value)[1] for key, value in policies.items()]
    source = PolicySourceBinding(
        producer_file="app.py",
        producer_sha256="a" * 64,
        configuration_file="policy.json",
        configuration_sha256="b" * 64,
        policy_sha256=original.policy_sha256,
        source_tree_hash="c" * 64,
        commit="d" * 40,
        workflow=original.workflow,
        max_input_bytes=original.max_input_bytes,
    )
    execution = SourcePolicyExecutionSpec(
        source=source,
        reference=references[0],
        candidates=references[1:],
        transport="scripted_test_only",
    )
    models = policy_descriptors(execution, policies)
    plan = ExperimentPlan(
        workflow=original.workflow,
        dataset_hash="e" * 64,
        baseline=models["reference"],
        candidates=[models[key] for key in policies if key != "reference"],
        metric={"kind": "exact_label", "minimum": 1, "min_test_samples": 3, "min_test_groups": 3},
        budget={"max_usd": "100", "max_requests": 500},
        approved_providers=["openai"],
        split="validation",
        retain_case_outputs=True,
        content_nonce=uuid4(),
        evaluator_version="deterministic-v19",
        source_policy_execution=execution,
    )
    specification = SourcePolicyProgram(
        dataset_id=uuid4(),
        baseline_snapshot_id=uuid4(),
        environment="staging",
        template=plan,
        generator=NativeGeneratorSpec(
            model=ModelSpec(
                key="generator",
                provider="openai",
                model="gpt-4.1-mini",
                max_input_tokens=100000,
                max_output_tokens=1024,
            ),
            transport="scripted_test_only",
            max_output_tokens=64,
            rounds=2,
            variants_per_round=2,
        )
        if generate
        else None,
        budget={"max_usd": "100", "max_requests": requests, "max_wall_seconds": 300},
        conservative_savings_per_success_usd="0.1",
        forecast_successful_tasks=100000,
    )
    items = []
    for split in ("train", "validation", "test"):
        for index in range(4):
            # Within-split exact repetitions are ONE independent input family.
            text = f"{split}-" + str(index if index < 3 else 0)
            items.append({"split": split, "group_hash": text, "case": {"input": text, "gold": "A"}})
    return specification, {"immutable_hash": plan.dataset_hash, "items": items}, policies


def compile_(prepared, baseline=Decimal("1"), independent=True):
    specification, data, policies = prepared
    return compile_policy_program(
        specification, data, policies, baseline, independent_labels=independent
    )


def test_one_cap_reserves_static_generation_training_routing_and_full_untouched_confirmation():
    value = program()
    with localcontext() as context:
        context.prec = 6
        result = compile_(value)
    assert result["eligible"] and result["selected_control_keys"] == ["cheap", "routed-cache"]
    assert set(result["phase_upper_usd"]) == {
        "static_validation",
        "generation_if_controls_fail",
        "generated_validation_if_controls_fail",
        "training_if_search_fails",
        "routed_validation_if_search_fails",
        "untouched_confirmation",
    }
    assert all(Decimal(cost) > 0 for cost in result["phase_upper_usd"].values())
    assert Decimal(result["worst_case_native_and_cache_usd"]) == sum(
        (Decimal(cost) for cost in result["phase_upper_usd"].values()), Decimal(0)
    )
    assert result["dataset_split_counts"] == {"train": 4, "validation": 4, "test": 4}
    assert result["max_requests"] == 78
    assert result["cache_trial_eligibility"]["routed-cache"]["potential_exact_hits"] == 1
    assert result["cache_trial_eligibility"]["routed-cache"]["production_hit_rate"] is None
    assert result["confirmation_sample_reduction"] is False
    assert result["full_task_cost_advantage"] is None and result["execution_authorized"] is False


def test_unknown_economics_or_missing_independence_never_authorize_a_smaller_trial():
    for baseline, independent in ((None, True), (Decimal("1"), False)):
        result = compile_(program(), baseline, independent)
        assert not result["eligible"] and result["max_requests"] is None
        assert result["dataset_split_counts"]["test"] == 4 and result["reasons"]
    result = compile_(program(requests=4))
    assert not result["eligible"] and result["confirmation_sample_reduction"] is False
    assert result["reasons"] == ["complete_program_exceeds_opportunity_or_request_cap"]


def test_no_measured_repetitions_omits_cache_before_approval_and_never_invents_production_hits():
    specification, data, policies = program()
    for index, item in enumerate(data["items"]):
        item["case"]["input"] += str(index)
    result = compile_((specification, data, policies))
    assert result["eligible"] and result["selected_control_keys"] == ["cheap"]
    assert result["omitted_controls"]["routed-cache"] == "no_repeated_frozen_validation_inputs"
    assert result["cache_trial_eligibility"]["routed-cache"]["potential_exact_hits"] == 0


def test_family_overlap_or_source_bounds_cannot_be_resolved_by_silent_case_removal():
    value = program()
    value[1]["items"][4]["group_hash"] = value[1]["items"][0]["group_hash"]
    with pytest.raises(ValueError, match="FAMILY_SPANS_DATASET_SPLITS"):
        compile_(value)
    value = program()
    value[1]["items"][0]["case"]["input"] = "é" * 100
    with pytest.raises(ValueError, match="SOURCE_INPUT_OR_NATIVE_BOUND_EXCEEDED"):
        compile_(value)


def test_initial_reference_must_be_the_original_native_source_and_money_exact():
    specification, data, policies = program()
    policies["reference"].strategy = policies["cheap"].strategy
    with pytest.raises(ValueError, match="FROZEN_POLICY_OR_CACHE_CHANGED"):
        compile_((specification, data, policies))
    raw = program()[0].model_dump(mode="json")
    raw["conservative_savings_per_success_usd"] = 0.1
    with pytest.raises(ValueError, match="EXACT_DECIMAL_MONEY_REQUIRED"):
        SourcePolicyProgram.model_validate(raw)


def test_next_cycle_uses_current_native_source_as_reference_instead_of_its_historical_anchor():
    specification, data, policies = program(cached=False, generate=False)
    original = policies["reference"].model_dump(mode="json")
    original["strategy"].update(model="gpt-4.1-mini", instruction="Current owned native revision")
    policies["reference"] = type(policies["reference"]).model_validate(original)
    cheaper = policies["cheap"].model_dump(mode="json")
    cheaper["strategy"].update(
        model="gpt-4.1-mini", instruction="Current owned native revision", max_output_tokens=16
    )
    policies["cheap"] = type(policies["cheap"]).model_validate(cheaper)
    references = [prepare_policy_payload(key, uuid4(), value)[1] for key, value in policies.items()]
    raw = specification.model_dump(mode="json")
    execution = raw["template"]["source_policy_execution"]
    execution["source"]["policy_sha256"] = policies["reference"].policy_sha256
    execution["reference"] = references[0].model_dump(mode="json")
    execution["candidates"] = [references[1].model_dump(mode="json")]
    bound = SourcePolicyExecutionSpec.model_validate(execution)
    descriptions = policy_descriptors(bound, policies)
    raw["template"]["baseline"] = descriptions["reference"].model_dump(mode="json")
    raw["template"]["candidates"] = [descriptions["cheap"].model_dump(mode="json")]
    current = SourcePolicyProgram.model_validate(raw)
    result = compile_((current, data, policies))
    assert result["eligible"] and result["selected_control_keys"] == ["cheap"]
    assert policies["reference"].baseline.model == "gpt-4.1"
    assert current.template.baseline.model == "gpt-4.1-mini"
