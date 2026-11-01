"""Same source request, conservative native/cache bounds and immutable leaf identities."""

from decimal import Decimal, localcontext

import pytest
from test_policy_execution_artifacts import artifacts

from cutback.evals.experiments import upper_cost
from cutback.evals.policy_artifacts import decode_policy_bundle
from cutback.evals.policy_models import (
    policy_descriptors,
    policy_leaf_models,
    policy_upper_costs,
    resolve_policy_target,
)


def compiled():
    execution, reference, candidate = artifacts()
    policies = decode_policy_bundle(
        {execution.reference.payload_id: reference, execution.candidates[0].payload_id: candidate},
        execution,
    )
    return execution, policies


def test_resolved_native_leaves_match_the_actual_source_request_and_cache_contract():
    execution, policies = compiled()
    descriptions = policy_descriptors(execution, policies)
    allowed = policy_leaf_models(execution, policies)
    for text in ("owned short", "x" * 65):
        for reference in [execution.reference, *execution.candidates]:
            policy = policies[reference.key]
            model = resolve_policy_target(execution, policies, descriptions[reference.key], text)
            expected = policy.request(text)
            assert model in allowed[reference.key]
            assert (model.model, model.instruction, model.max_output_tokens) == (
                expected["model"],
                expected["instructions"],
                expected["max_output_tokens"],
            )
            assert model.exact_cache == policy.cache_execution
            assert model.key.startswith("policy-") and "PRIVATE" not in model.key
    assert {value.key for value in allowed["reference"]}.isdisjoint(
        {value.key for value in allowed["routed-cache"]}
    )


def test_upper_costs_include_cache_operations_without_ambient_decimal_rounding():
    execution, policies = compiled()
    with localcontext() as context:
        context.prec = 6
        costs = policy_upper_costs(execution, policies)
    original = policies["routed-cache"].strategy
    assert costs["routed-cache"] == upper_cost(original) + Decimal("0.000002")
    assert costs["reference"] == upper_cost(policies["reference"].strategy)


def test_mutated_descriptor_policy_or_input_cannot_reach_an_approved_leaf():
    execution, policies = compiled()
    descriptor = policy_descriptors(execution, policies)["routed-cache"]
    with pytest.raises(ValueError, match="DESCRIPTOR_CHANGED"):
        resolve_policy_target(
            execution, policies, descriptor.model_copy(update={"model": "changed"}), "owned"
        )
    with pytest.raises(ValueError, match="BOUNDED_TEXT_REQUIRED"):
        resolve_policy_target(execution, policies, descriptor, "x" * 129)
    policies["routed-cache"].strategy.routes[0].target.instruction = "Changed private body"
    with pytest.raises(ValueError, match="FROZEN_POLICY_OR_CACHE_CHANGED"):
        resolve_policy_target(execution, policies, descriptor, "owned")
