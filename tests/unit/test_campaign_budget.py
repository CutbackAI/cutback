from decimal import Decimal

import pytest
from test_campaign_contracts import campaign

from cutback.evals.campaign_budget import compile_campaign


def dataset():
    return {
        "immutable_hash": "a" * 64,
        "items": [
            {"split": split, "case": {"input": f"Synthetic {split} {index}"}}
            for split in ("train", "validation", "test")
            for index in range(3)
        ],
    }


def test_compilation_includes_proposer_simple_baseline_all_targets_and_confirmation():
    value = campaign(conservative_savings_per_success_usd="0.1", forecast_successful_tasks=1000)
    receipt = compile_campaign(value, dataset(), Decimal("1"))
    assert receipt["eligible"] and receipt["rounds"] == 1 and receipt["variants_per_round"] == 2
    assert receipt["max_requests"] == 1 + 3 * 4 + 3 * 2
    assert (
        Decimal(receipt["worst_case_modeled_usd"])
        <= Decimal(receipt["effective_max_usd"])
        <= value.budget.max_usd
    )
    assert not receipt["paid_calls_authorized"] and receipt["provider_calls"] == 0


def test_compile_scales_effort_down_without_reducing_heldout_samples():
    value = campaign(conservative_savings_per_success_usd="0.1", forecast_successful_tasks=1000)
    raw = value.model_dump(mode="json")
    raw["generator"].update(rounds=3, variants_per_round=4)
    raw["budget"]["max_requests"] = 16  # One generator, three models x3 validation, two x3 test.
    value = type(value).model_validate(raw)
    receipt = compile_campaign(value, dataset(), Decimal("1"))
    assert receipt["eligible"] and receipt["rounds"] == receipt["variants_per_round"] == 1
    assert receipt["max_requests"] == 16
    assert receipt["split_samples"] == {"train": 3, "validation": 3, "test": 3}


@pytest.mark.parametrize(
    "baseline,savings,volume",
    [
        (None, "0.1", 100),
        (Decimal("1"), None, 100),
        (Decimal("1"), "0.1", None),
        (Decimal("1"), "2", 100),
        (Decimal("1"), "0", 100),
    ],
)
def test_unknown_or_impossible_opportunity_abstains(baseline, savings, volume):
    value = campaign(conservative_savings_per_success_usd=savings, forecast_successful_tasks=volume)
    receipt = compile_campaign(value, dataset(), baseline)
    assert not receipt["eligible"] and receipt["max_requests"] == 0
    assert receipt["worst_case_modeled_usd"] is None and receipt["reasons"]


def test_instruction_byte_bounds_are_costed_before_approval_and_boolean_budgets_rejected():
    value = campaign()
    raw = value.model_dump(mode="json")
    raw["generator"]["max_instruction_bytes"] = 16000
    with pytest.raises(ValueError, match="TARGET_INPUT_BOUND_TOO_SMALL"):
        compile_campaign(type(value).model_validate(raw), dataset(), Decimal("1"))
    for key in ("max_requests", "max_wall_seconds"):
        raw = value.model_dump(mode="json")
        raw["budget"][key] = True
        with pytest.raises(ValueError):
            type(value).model_validate(raw)


def test_compiler_reduces_rounds_before_spend_when_future_context_would_overflow():
    value = campaign(conservative_savings_per_success_usd="0.1", forecast_successful_tasks=1000)
    raw = value.model_dump(mode="json")
    raw["generator"].update(rounds=3, variants_per_round=1)
    low = compile_campaign(type(value).model_validate(raw), dataset(), Decimal("1"))
    assert low["eligible"] and low["rounds"] == 1
    assert low["generator_input_upper_bytes"] <= raw["generator"]["model"]["max_input_tokens"]
    raw["generator"]["model"]["max_input_tokens"] = 100000
    full = compile_campaign(type(value).model_validate(raw), dataset(), Decimal("1"))
    assert full["rounds"] == 3
    assert full["generator_input_upper_bytes"] > low["generator_input_upper_bytes"]


def test_future_context_bound_covers_json_escaping_all_versions_and_aggregate_feedback():
    from uuid import uuid4

    from cutback.evals.campaigns import (
        generation_input,
        generation_input_upper_bytes,
        generation_request_bytes,
    )

    value = campaign()
    raw = value.model_dump(mode="json")
    raw["generator"].update(rounds=3, variants_per_round=4, max_instruction_bytes=128)
    raw["generator"]["model"]["max_input_tokens"] = 100000
    value = type(value).model_validate(raw)
    items = [
        {
            "split": "train",
            "canonical_family_id": str(uuid4()),
            "case": {"id": str(i), "input": "\0" * (i + 1), "gold": "é" * 10},
        }
        for i in range(10)
    ]
    versions = [
        {
            **value.template.baseline.model_dump(mode="json"),
            "key": f"generated-r{r}-{v}",
            "instruction": "\0" * 128,
            "max_output_tokens": 16,
        }
        for r in (1, 2)
        for v in (1, 2, 3, 4)
    ]
    feedback = [
        {
            "candidate_key": "x" * 200,
            "quality_gate_passed": False,
            "sample_metric_min": 1e-200,
            "cost_complete": False,
            "sample_cost_per_request_usd": "999999999999.999999999999",
        }
    ] * 10
    text, _ = generation_input(value, items, feedback, prior_variants=versions)
    assert generation_request_bytes(value, text) <= generation_input_upper_bytes(
        value, items, rounds=3, variants=4
    )
