"""Owned wire contract, train-only input, source-preserving instructions and no replay variants."""

import json
from uuid import uuid4

import pytest
from test_source_policy_program_budget import program

from cutback.evals.campaigns import proposal_model
from cutback.evals.policy_generation import (
    PolicyGenerationPlan,
    decode_policy_proposals,
    generation_input,
    input_upper_bytes,
)


def response(**changes):
    return json.dumps(
        {
            "proposals": [
                {
                    "seed_key": "cheap",
                    "instruction": "Owned shorter instruction.",
                    "max_output_tokens": 16,
                    **changes,
                }
            ]
        }
    )


def test_generation_sends_only_train_examples_under_the_whole_input_bound():
    specification, data, policies = program()
    text, families = generation_input(specification, data, policies, 1)
    body = json.loads(text)
    assert len(families) == len(body["training"]) == 3
    assert all(value["input"].startswith("train-") for value in body["training"])
    assert "test-" not in text and "validation-" not in text
    assert {value["key"] for value in body["seeds"]} == {"reference", "cheap"}
    assert (
        len(text.encode())
        < input_upper_bytes(specification, data, policies)
        < specification.generator.model.max_input_tokens
    )
    different = dict(data, immutable_hash="a" * 64)
    with pytest.raises(ValueError, match="FROZEN_DATASET_REQUIRED"):
        generation_input(specification, different, policies, 1)


def test_generated_variants_preserve_source_and_native_fields_except_approved_instruction_cap():
    specification, _, policies = program()
    candidates = decode_policy_proposals(response(), specification, policies, 1)
    assert list(candidates) == ["policy-generated-r1-1"]
    candidate = candidates["policy-generated-r1-1"]
    assert candidate.baseline == policies["cheap"].baseline
    assert candidate.strategy.model == policies["cheap"].strategy.model
    assert candidate.strategy.max_output_tokens == 16
    assert candidate.strategy.instruction == "Owned shorter instruction."
    assert candidate.cache_execution is None
    with pytest.raises(ValueError, match="DUPLICATED_RUNTIME_CONFIGURATION"):
        decode_policy_proposals(response(), specification, policies, 2, previous=[candidate])


@pytest.mark.parametrize(
    "text",
    [
        '{"proposals":[],"proposals":[]}',
        '{"proposals":NaN}',
        (
            '{"proposals":[{"seed_key":"cheap","instruction":"private",'
            '"max_output_tokens":32,"command":"run me"}]}'
        ),
        "x" * 100001,
    ],
    ids=["duplicate-key", "nonfinite", "extra-command", "oversized"],
)
def test_untrusted_generator_json_cannot_add_commands_or_ambiguous_fields(text):
    specification, _, policies = program()
    with pytest.raises(ValueError, match="BOUNDED_RESPONSE_SCHEMA_REQUIRED") as failure:
        decode_policy_proposals(text, specification, policies, 1)
    assert "private" not in str(failure.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"seed_key": "routed-cache"},
        {"max_output_tokens": 128},
        {"instruction": "é" * 600},
    ],
)
def test_policy_proposals_cannot_escape_seed_provider_or_instruction_output_bounds(changes):
    specification, _, policies = program()
    with pytest.raises(ValueError, match="OUTSIDE_FROZEN_FRAME"):
        decode_policy_proposals(response(**changes), specification, policies, 1)


def test_complete_generation_history_has_a_pre_call_bound_and_cannot_inject_another_model():
    specification, data, policies = program()
    previous = decode_policy_proposals(response(), specification, policies, 1)[
        "policy-generated-r1-1"
    ]
    changed = previous.model_copy(deep=True)
    changed.strategy.model = "gpt-4.1-nano"
    with pytest.raises(ValueError, match="PREVIOUS_VARIANT_OUTSIDE_FROZEN_FRAME"):
        generation_input(specification, data, policies, 2, previous=[changed])
    with pytest.raises(ValueError, match="HISTORY_BOUND"):
        generation_input(specification, data, policies, 1, previous=[previous])
    specification.generator.model.max_input_tokens = 5000
    with pytest.raises(ValueError, match="COMPLETE_INPUT_BOUND_EXCEEDED"):
        generation_input(specification, data, policies, 2, previous=[previous])


def test_generation_plan_requires_exact_owned_wire_schema_and_one_physical_call():
    specification, _, _ = program()
    value = PolicyGenerationPlan(
        program_id=uuid4(),
        program_sha256="a" * 64,
        round_index=1,
        workflow=specification.template.workflow,
        dataset_hash=specification.template.dataset_hash,
        input_payload_id=uuid4(),
        input_sha256="b" * 64,
        baseline=proposal_model(specification.generator),
        budget={"max_usd": "1", "max_requests": 1},
    )
    raw = value.model_dump(mode="json")
    raw["baseline"]["request_contract"]["options"]["text"]["format"]["name"] = "changed"
    with pytest.raises(ValueError, match="OWNED_WIRE_CONTRACT_REQUIRED"):
        PolicyGenerationPlan.model_validate(raw)
