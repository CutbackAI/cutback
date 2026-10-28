import json
from uuid import uuid4

import pytest

from cutback.evals.campaigns import (
    ImprovementCampaign,
    decode_proposals,
    generation_input,
    proposal_model,
)


def campaign(**changes):
    value = {
        "dataset_id": str(uuid4()),
        "baseline_snapshot_id": str(uuid4()),
        "environment": "staging",
        "template": {
            "workflow": "synthetic.classify",
            "dataset_hash": "a" * 64,
            "split": "validation",
            "baseline": {
                "key": "baseline",
                "provider": "openai",
                "model": "gpt-4.1",
                "max_output_tokens": 256,
            },
            "candidates": [
                {
                    "key": "simple-downgrade",
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "max_output_tokens": 256,
                }
            ],
            "metric": {"kind": "exact_label", "minimum": 1},
            "budget": {"max_usd": "10", "max_requests": 100},
            "approved_providers": ["openai"],
        },
        "generator": {"model": {"key": "proposer", "provider": "openai", "model": "gpt-4.1-mini"}},
        "budget": {"max_usd": "10", "max_requests": 100},
        **changes,
    }
    return ImprovementCampaign.model_validate(value)


def proposal(**changes):
    return {
        "seed_key": "simple-downgrade",
        "instruction": "Return the required declared label.",
        "max_output_tokens": 128,
        **changes,
    }


def test_proposal_freezes_only_approved_fields_and_preserves_template():
    spec = campaign()
    before = spec.model_dump(mode="json")
    generated = decode_proposals(json.dumps({"proposals": [proposal()]}), spec, round_index=1)
    assert spec.model_dump(mode="json") == before
    assert len(generated) == 1 and generated[0].instruction == proposal()["instruction"]
    seed = spec.template.candidates[0].model_dump(mode="json")
    result = generated[0].model_dump(mode="json")
    for field in seed.keys() - {"key", "instruction", "max_output_tokens"}:
        assert result[field] == seed[field]


@pytest.mark.parametrize(
    "changes",
    [
        {"seed_key": "not-approved"},
        {"max_output_tokens": 512},
        {"max_output_tokens": True},
        {"provider": "local"},
        {"endpoint": "https://untrusted.example"},
        {"tools": [{"name": "shell"}]},
        {"instruction": "x" * 16001},
        {"instruction": "\U0001f30e" * 8000},
    ],
)
def test_unapproved_generated_scope_or_malformed_proposal_is_rejected(changes):
    with pytest.raises(ValueError):
        decode_proposals(
            json.dumps({"proposals": [proposal(**changes)]}), campaign(), round_index=1
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"proposals":[],"proposals":[]}',
        '{"proposals":NaN}',
        "not JSON",
        "[" * 2000,
    ],
)
def test_ambiguous_nonstandard_or_deep_json_never_becomes_a_candidate(text):
    with pytest.raises(ValueError, match="SCHEMA_INVALID"):
        decode_proposals(text, campaign(), round_index=1)


def test_duplicate_variants_or_unapproved_round_are_rejected():
    spec = campaign()
    with pytest.raises(ValueError, match="DUPLICATE_CONFIGURATION"):
        decode_proposals(json.dumps({"proposals": [proposal(), proposal()]}), spec, round_index=1)
    with pytest.raises(ValueError, match="ROUND_OUTSIDE"):
        decode_proposals(json.dumps({"proposals": [proposal()]}), spec, round_index=2)


def test_test_split_cannot_be_used_for_adaptive_generation():
    spec = campaign().model_dump(mode="json")
    spec["template"]["split"] = "test"
    with pytest.raises(ValueError, match="VALIDATION_TEMPLATE"):
        ImprovementCampaign.model_validate(spec)


def test_generator_cannot_change_its_wire_contract_or_hide_instructions():
    spec = campaign().model_dump(mode="json")
    spec["generator"]["contract_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="REAPPROVAL_REQUIRED"):
        ImprovementCampaign.model_validate(spec)


def test_generator_request_uses_strict_schema_and_only_frozen_train_content():
    spec = campaign()
    family = str(uuid4())
    items = [
        {
            "split": "train",
            "canonical_family_id": family,
            "case": {"id": "train-one", "input": "Owned training input", "gold": "A"},
        },
        {
            "split": "train",
            "canonical_family_id": family,
            "case": {
                "id": "train-two",
                "input": "Duplicate family must not multiply training",
                "gold": "B",
            },
        },
        {
            "split": "validation",
            "case": {"id": "validation", "input": "VALIDATION_CONTENT_FORBIDDEN", "gold": "V"},
        },
        {"split": "test", "case": {"id": "test", "input": "TEST_CONTENT_FORBIDDEN", "gold": "T"}},
    ]
    text, families = generation_input(spec, items)
    assert families == [family]
    assert "Owned training input" in text and "Duplicate family" not in text
    assert "CONTENT_FORBIDDEN" not in text
    model = proposal_model(spec.generator)
    assert model.request_contract.render(text) == text
    formatting = model.request_contract.options["text"]["format"]
    assert formatting["strict"] and formatting["schema"]["additionalProperties"] is False
    assert "tools" not in model.request_contract.options and "endpoint" not in text


def test_training_lineage_feedback_content_and_request_bounds_fail_before_execution():
    spec = campaign()
    item = {"split": "train", "case": {"id": "train", "input": "owned", "gold": "A"}}
    with pytest.raises(ValueError, match="TRAINING_LINEAGE"):
        generation_input(spec, [item])
    item["canonical_family_id"] = str(uuid4())
    with pytest.raises(ValueError):
        generation_input(
            spec, [item], [{"candidate_key": "baseline", "output_text": "content is forbidden"}]
        )
    item["case"]["input"] = "x" * 100000
    with pytest.raises(ValueError, match="FROZEN_TOKEN_BOUND"):
        generation_input(spec, [item])
    spec = campaign().model_dump(mode="json")
    spec["generator"]["model"]["instruction"] = "A hidden directive is not an adapter contract."
    with pytest.raises(ValueError, match="QUALIFIED_OPENAI_TEXT"):
        ImprovementCampaign.model_validate(spec)
