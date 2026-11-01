"""Encrypted-content identities, public projection and conservative operation money."""

import copy
import json
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from test_source_improvement_policy import policy

from cutback.core.policy_execution import PolicyExecutionRef, SourcePolicyExecutionSpec
from cutback.core.policy_producer import PolicySourceBinding
from cutback.evals.policy_artifacts import (
    decode_policy_bundle,
    decode_policy_payload,
    prepare_policy_payload,
)


def artifacts():
    reference_policy, candidate_policy = policy(), policy(routed=True, cached=True)
    raw_reference, reference = prepare_policy_payload("reference", uuid4(), reference_policy)
    raw_candidate, candidate = prepare_policy_payload("routed-cache", uuid4(), candidate_policy)
    source = PolicySourceBinding(
        producer_file="app.py",
        producer_sha256="a" * 64,
        configuration_file="policy.json",
        configuration_sha256="b" * 64,
        policy_sha256=reference_policy.policy_sha256,
        source_tree_hash="c" * 64,
        commit="d" * 40,
        workflow=reference_policy.workflow,
        max_input_bytes=reference_policy.max_input_bytes,
    )
    execution = SourcePolicyExecutionSpec(
        source=source, reference=reference, candidates=[candidate]
    )
    return execution, raw_reference, raw_candidate


def test_rekeyed_native_or_redundant_routing_is_not_a_distinct_runtime_trial():
    execution, raw_reference, _ = artifacts()
    reference = decode_policy_payload(raw_reference, execution.reference, execution)
    duplicate = reference.model_copy(deep=True)
    duplicate.strategy.key = "different-metadata-only"
    raw, ref = prepare_policy_payload("rekeyed", uuid4(), duplicate)
    changed = execution.model_copy(update={"candidates": [ref]})
    assert duplicate.policy_sha256 != reference.policy_sha256
    assert duplicate.behavior_sha256 == reference.behavior_sha256
    with pytest.raises(ValueError, match="DUPLICATED_RUNTIME_CONFIGURATION"):
        decode_policy_bundle(
            {execution.reference.payload_id: raw_reference, ref.payload_id: raw}, changed
        )
    from cutback.evals.experiments import ModelSpec

    routed = reference.model_copy(deep=True)
    routed.strategy = ModelSpec(
        key="redundant",
        provider="router",
        model="input-size-v1",
        routes=[
            {"max_input_bytes": 64, "target": duplicate.strategy},
            {"max_input_bytes": 128, "target": reference.strategy},
        ],
    )
    assert routed.behavior_sha256 == reference.behavior_sha256


def test_exact_payloads_and_public_projection_do_not_grant_content_or_execution():
    execution, raw_reference, raw_candidate = artifacts()
    assert decode_policy_payload(raw_reference, execution.reference, execution) == policy()
    assert decode_policy_payload(raw_candidate, execution.candidates[0], execution) == policy(
        routed=True, cached=True
    )
    public = json.dumps(execution.public_summary())
    assert "OWNED_PRIVATE" not in public and execution.reference.payload_sha256 not in public
    assert str(raw_reference["nonce"]) not in public
    assert execution.public_summary()["execution_authorized"] is False
    assert execution.reference.cache_cost_upper_per_case_usd == Decimal(0)
    assert execution.candidates[0].cache_cost_upper_per_case_usd == Decimal("0.000002")


@pytest.mark.parametrize("change", ["nonce", "workflow", "version", "instruction"])
def test_swapped_or_modified_payload_is_refused_without_echoing_content(change):
    execution, _, raw_candidate = artifacts()
    changed = copy.deepcopy(raw_candidate)
    if change == "nonce":
        changed["nonce"] = str(UUID(int=0))
    elif change == "workflow":
        changed["policy"]["workflow"] = "another-workflow"
    elif change == "version":
        changed["version"] = "unknown-payload-version"
    else:
        changed["policy"]["strategy"]["routes"][0]["target"]["instruction"] = "PRIVATE_TAMPERED"
    with pytest.raises(ValueError) as stopped:
        decode_policy_payload(changed, execution.candidates[0], execution)
    assert str(stopped.value) == "POLICY_ARTIFACT_BINDING_OR_FRAME_CHANGED"


def test_payload_metadata_cannot_understate_declared_cache_costs():
    execution, _, raw_candidate = artifacts()
    raw = execution.model_dump(mode="json")
    raw["candidates"][0]["cache_operation_upper_usd"] = "0"
    understated = SourcePolicyExecutionSpec.model_validate(raw)
    with pytest.raises(ValueError, match="BINDING_OR_FRAME_CHANGED"):
        decode_policy_payload(raw_candidate, understated.candidates[0], understated)


def test_missing_cache_price_stays_unknown_and_decimal_context_cannot_round_a_cap():
    values = {
        "key": "candidate",
        "payload_id": uuid4(),
        "payload_sha256": "a" * 64,
        "policy_sha256": "b" * 64,
        "cache_enabled": True,
    }
    assert PolicyExecutionRef(**values).cache_cost_upper_per_case_usd is None
    with localcontext() as context:
        context.prec = 6
        actual = PolicyExecutionRef(**values, cache_operation_upper_usd="0.123456789012")
        assert actual.cache_cost_upper_per_case_usd == Decimal("0.246913578024")
    with pytest.raises(ValueError, match="EXACT_DECIMAL"):
        PolicyExecutionRef(**values, cache_operation_upper_usd=0.1)


def test_duplicate_policy_or_payload_references_cannot_mask_a_distinct_trial():
    execution, _, _ = artifacts()
    raw = execution.model_dump(mode="json")
    raw["candidates"][0]["payload_id"] = str(execution.reference.payload_id)
    with pytest.raises(ValueError, match="DISTINCT_SOURCE_BOUND_REFERENCES"):
        SourcePolicyExecutionSpec.model_validate(raw)


def test_valid_payload_hashes_cannot_increase_the_source_family_output_ceiling():
    execution, reference, candidate = artifacts()
    forged = copy.deepcopy(candidate["policy"])
    forged["baseline"]["max_output_tokens"] = 128
    from cutback.core.improvement_policy import SourceImprovementPolicy

    body, identity = prepare_policy_payload(
        "routed-cache", uuid4(), SourceImprovementPolicy.model_validate(forged)
    )
    changed = execution.model_copy(update={"candidates": [identity]})
    # Each standalone immutable payload is well formed; the shared frame is not.
    assert decode_policy_payload(body, identity, changed).baseline.max_output_tokens == 128
    with pytest.raises(ValueError, match="CHANGED_REFERENCE_FRAME"):
        decode_policy_bundle(
            {execution.reference.payload_id: reference, identity.payload_id: body}, changed
        )


def test_bundle_requires_exact_known_payloads_and_preserves_both_policy_choices():
    execution, reference, candidate = artifacts()
    payloads = {
        execution.reference.payload_id: reference,
        execution.candidates[0].payload_id: candidate,
    }
    actual = decode_policy_bundle(payloads, execution)
    assert set(actual) == {"reference", "routed-cache"}
    with pytest.raises(ValueError, match="EXACT_BOUNDED_BUNDLE_REQUIRED"):
        decode_policy_bundle({**payloads, uuid4(): candidate}, execution)
