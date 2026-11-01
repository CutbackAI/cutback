"""A cached source's actual comparison and uncached training remain distinct and bound."""

from decimal import Decimal
from uuid import uuid4

import pytest
from test_policy_runtime_reference import frame
from test_source_improvement_policy import policy

from cutback.core.policy_execution import (
    PublicPolicyExecution,
    SourcePolicyExecutionSpec,
    uncached_shadow_reference,
)
from cutback.evals.experiments import upper_cost
from cutback.evals.policy_artifacts import decode_policy_bundle, prepare_policy_payload
from cutback.evals.policy_models import policy_leaf_models, policy_upper_costs


def prepared(*, requested=True, training=False, duplicate=False):
    original = policy(cached=True)
    original.strategy = original.baseline
    candidate = original.model_copy(update={"cache_execution": None}) if duplicate else policy()
    raw_original, reference = prepare_policy_payload("reference", uuid4(), original)
    raw_candidate, alternative = prepare_policy_payload("candidate", uuid4(), candidate)
    source = frame()[1].model_copy(update={"policy_sha256": original.policy_sha256})
    reference = (
        reference.model_copy(update={"cache_requested": True})
        if requested and not training
        else uncached_shadow_reference(reference)
    )
    execution = SourcePolicyExecutionSpec(
        version="cutback.source-policy-execution.v2",
        phase="training" if training else "evaluation",
        source=source,
        reference=reference,
        candidates=[alternative],
    )
    payloads = {reference.payload_id: raw_original, alternative.payload_id: raw_candidate}
    return execution, payloads, original, candidate


@pytest.mark.parametrize("requested", [False, True])
def test_current_cached_configuration_keeps_exact_payload_and_measured_activation(requested):
    execution, payloads, original, _ = prepared(requested=requested)
    decoded = decode_policy_bundle(payloads, execution)
    assert decoded["reference"] == original and original.cache_execution is not None
    assert (
        execution.reference.policy_sha256
        == execution.source.policy_sha256
        == original.policy_sha256
    )
    models = policy_leaf_models(execution, decoded)
    leaf = models["reference"][0]
    assert (leaf.exact_cache is not None) is requested
    plain = upper_cost(leaf.model_copy(update={"exact_cache": None}))
    assert policy_upper_costs(execution, decoded)["reference"] == plain + (
        Decimal("0.000002") if requested else 0
    )
    summary = execution.public_summary()
    assert summary["cache_activation"] == {"reference": requested, "candidate": False}
    assert not summary["uncached_training_shadow"]
    assert PublicPolicyExecution.model_validate(summary).version == execution.version


def test_training_shadow_disables_cache_without_replacing_the_current_source_policy():
    execution, payloads, original, _ = prepared(training=True)
    decoded = decode_policy_bundle(payloads, execution)
    assert decoded["reference"].cache_execution is not None
    assert all(
        model.exact_cache is None
        for values in policy_leaf_models(execution, decoded).values()
        for model in values
    )
    assert execution.reference.policy_sha256 == original.policy_sha256
    assert execution.reference.cache_requested is False and not execution.reference.cache_enabled
    assert execution.public_summary()["uncached_training_shadow"] is True
    assert PublicPolicyExecution.model_validate(execution.public_summary()).phase == "training"


def test_v1_cannot_silently_adopt_v2_activation_and_training_cannot_use_active_cache():
    execution, _, _, _ = prepared()
    with pytest.raises(ValueError):
        SourcePolicyExecutionSpec.model_validate(
            {**execution.model_dump(mode="json"), "version": "cutback.source-policy-execution.v1"}
        )
    with pytest.raises(ValueError):
        SourcePolicyExecutionSpec.model_validate(
            {**execution.model_dump(mode="json"), "phase": "training"}
        )
    raw = execution.model_dump(mode="json")
    raw["reference"].pop("cache_requested")
    with pytest.raises(ValueError):
        SourcePolicyExecutionSpec.model_validate(raw)


def test_effectively_duplicate_native_behavior_is_not_a_distinct_training_control():
    execution, payloads, _, _ = prepared(requested=True, duplicate=True)
    assert decode_policy_bundle(
        payloads, execution
    )  # Active cache versus uncached control differs.
    raw = execution.model_dump(mode="json")
    raw.update(
        phase="training",
        reference=uncached_shadow_reference(execution.reference).model_dump(mode="json"),
    )
    shadow = SourcePolicyExecutionSpec.model_validate(raw)
    with pytest.raises(ValueError, match="DUPLICATED_RUNTIME_CONFIGURATION"):
        decode_policy_bundle(payloads, shadow)
