"""Cached-current instruction search preserves the frozen cache contract and train-only input."""

import json

import pytest
from test_source_policy_cached_budget import cached_program
from test_source_policy_generation import response

from cutback.evals.policy_generation import (
    decode_policy_proposals,
    generation_input,
    input_upper_bytes,
    seeds,
)


@pytest.mark.parametrize("requested", [False, True])
def test_cached_current_search_keeps_exact_configuration_and_observed_activation(requested):
    specification, data, policies = cached_program(
        requested=requested, cached_controls=True, generate=True
    )
    assert set(seeds(policies, specification=specification)) == {"reference", "cheap"}
    assert seeds(policies) == {}  # The legacy generator does not gain a new seed surface.
    text, families = generation_input(specification, data, policies, 1)
    document = json.loads(text)
    assert families and all(row["input"].startswith("train-") for row in document["training"])
    assert "test-" not in text and "validation-" not in text
    assert len(text.encode()) < input_upper_bytes(specification, data, policies)
    generated = decode_policy_proposals(response(), specification, policies, 1)
    value = generated["policy-generated-r1-1"]
    assert value.cache_execution == policies["reference"].cache_execution
    assert value.runtime_cache_activation == policies["reference"].runtime_cache_activation
    assert value.baseline == policies["reference"].baseline
    assert specification.template.source_policy_execution.reference.cache_requested is requested
    generation_input(specification, data, policies, 2, previous=[value])
    changed = value.model_copy(update={"cache_execution": None})
    with pytest.raises(ValueError, match="PREVIOUS_VARIANT_OUTSIDE_FROZEN_FRAME"):
        generation_input(specification, data, policies, 2, previous=[changed])


def test_cached_current_generator_cannot_borrow_a_different_cache_contract():
    specification, _, policies = cached_program(cached_controls=True, generate=True)
    policies["cheap"] = policies["cheap"].model_copy(update={"cache_execution": None})
    assert set(seeds(policies, specification=specification)) == {"reference"}
    with pytest.raises(ValueError, match="FROZEN_SOURCE_POLICIES_REQUIRED"):
        decode_policy_proposals(response(), specification, policies, 1)
