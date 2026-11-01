"""Derive exact native leaves from a validated private policy bundle.

No provider execution or approval occurs here. New evaluators/ledgers must use
these same derived leaves; ordinary routing/cache support guards stay unchanged.
"""

from decimal import Context, Decimal, localcontext

from cutback.core.policy_execution import (
    SourcePolicyExecutionSpec,
    effective_policy_behavior,
    qualified_cache,
)
from cutback.core.request_profiles import fingerprint
from cutback.evals.experiments import ModelSpec, provider_models, upper_cost


def checked_policies(execution: SourcePolicyExecutionSpec, policies: dict):
    references = [execution.reference, *execution.candidates]
    if not isinstance(policies, dict) or set(policies) != {value.key for value in references}:
        raise ValueError("POLICY_MODELS_EXACT_VALIDATED_BUNDLE_REQUIRED")
    for reference in references:
        policy = policies[reference.key]
        try:
            cached = qualified_cache(reference, policy, execution)
        except ValueError:
            raise ValueError("POLICY_MODELS_FROZEN_POLICY_OR_CACHE_CHANGED") from None
        if (
            policy.policy_sha256 != reference.policy_sha256
            or policy.workflow != execution.source.workflow
            or policy.max_input_bytes != execution.source.max_input_bytes
            or reference.cache_enabled != (cached is not None)
            or reference.cache_operation_upper_usd
            != (cached.operation_cost_upper_usd if cached else None)
        ):
            raise ValueError("POLICY_MODELS_FROZEN_POLICY_OR_CACHE_CHANGED")
    original = policies[execution.reference.key]
    if len(
        {
            effective_policy_behavior(reference, policies[reference.key], execution)
            for reference in references
        }
    ) != len(policies):
        raise ValueError("POLICY_MODELS_DUPLICATED_RUNTIME_CONFIGURATION")
    if any(
        policy.baseline != original.baseline
        or policy.max_instruction_bytes != original.max_instruction_bytes
        for policy in policies.values()
    ):
        raise ValueError("POLICY_MODELS_REFERENCE_FRAME_CHANGED")
    return references


def policy_descriptors(execution: SourcePolicyExecutionSpec, policies: dict):
    """Ordinary frozen comparison descriptors; cache authority stays in its binding."""
    references = checked_policies(execution, policies)
    return {
        reference.key: ModelSpec.model_validate(
            {**policies[reference.key].strategy.model_dump(mode="json"), "key": reference.key}
        )
        for reference in references
    }


def native_leaf(reference, policy, target, *, execution=None):
    # Include the whole target identity: distinct instructions can share a local
    # target key without colliding or exposing prompt text in execution metadata.
    alias = (
        "policy-"
        + fingerprint(
            {
                "version": "source-policy-leaf-v1",
                "reference": reference.key,
                "policy_sha256": reference.policy_sha256,
                "target": target.model_dump(mode="json"),
            }
        )[:24]
    )
    cache = qualified_cache(reference, policy, execution)
    return ModelSpec.model_validate(
        {
            **target.model_dump(mode="json"),
            "key": alias,
            "exact_cache": cache.model_dump(mode="json") if cache is not None else None,
        }
    )


def policy_leaf_models(execution: SourcePolicyExecutionSpec, policies: dict):
    references = checked_policies(execution, policies)
    return {
        reference.key: [
            native_leaf(reference, policies[reference.key], target, execution=execution)
            for target in provider_models(policies[reference.key].strategy)
        ]
        for reference in references
    }


def resolve_policy_target(execution, policies, descriptor: ModelSpec, text: str):
    descriptors = policy_descriptors(execution, policies)
    expected = descriptors.get(descriptor.key)
    if expected is None or expected != descriptor:
        raise ValueError("POLICY_MODELS_COMPARISON_DESCRIPTOR_CHANGED")
    reference = next(
        value
        for value in [execution.reference, *execution.candidates]
        if value.key == descriptor.key
    )
    policy = policies[reference.key]
    return native_leaf(reference, policy, policy.target(text), execution=execution)


def policy_upper_costs(execution: SourcePolicyExecutionSpec, policies: dict) -> dict[str, Decimal]:
    leaves = policy_leaf_models(execution, policies)
    with localcontext(Context(prec=80)):
        return {key: max(upper_cost(model) for model in values) for key, values in leaves.items()}
