"""The portfolio is frozen before approval and cannot reset the parent budget."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from test_campaign_budget import dataset
from test_campaign_contracts import campaign, proposal

from cutback.evals.campaign_budget import compile_campaign
from cutback.evals.campaigns import (
    ImprovementCampaign,
    automatic_static_portfolio,
    decode_proposals,
    generation_input,
    generation_input_upper_bytes,
    generation_request_bytes,
)


def portfolio(**changes):
    legacy = campaign()
    generator = legacy.generator.model_dump(mode="json")
    generator["model"]["max_input_tokens"] = 24000
    return campaign(
        **{
            "version": "cutback.improvement-campaign.v3",
            "static_portfolio": [
                m.model_dump(mode="json") for m in automatic_static_portfolio(legacy.template)
            ],
            "conservative_savings_per_success_usd": "0.1",
            "forecast_successful_tasks": 1000,
            "generator": generator,
            **changes,
        }
    )


def test_automatic_portfolio_preserves_native_contract_and_spans_model_and_output_bounds():
    spec = portfolio()
    targets = spec.static_portfolio
    assert 2 <= len(targets) <= 4
    assert any(m.model != spec.template.baseline.model for m in targets)
    assert any(m.max_output_tokens < spec.template.baseline.max_output_tokens for m in targets)
    for model in targets:
        assert model.request_contract == spec.template.baseline.request_contract
        assert model.instruction == spec.template.baseline.instruction
    raw = spec.model_dump(mode="json")
    assert ImprovementCampaign.model_validate(raw).model_dump(mode="json") == raw
    for version in ("cutback.improvement-campaign.v1", "cutback.improvement-campaign.v2"):
        old = campaign(version=version).model_dump(mode="json")
        assert "static_portfolio" not in old
        assert ImprovementCampaign.model_validate(old).model_dump(mode="json") == old


@pytest.mark.parametrize(
    "mutation", ["duplicate", "instruction", "input", "wider", "key", "version"]
)
def test_portfolio_rejects_duplicate_content_scope_changes_and_legacy_envelope_injection(mutation):
    raw = portfolio().model_dump(mode="json")
    target = raw["static_portfolio"][0]
    if mutation == "duplicate":
        raw["static_portfolio"][1] = {**target, "key": "duplicate"}
    elif mutation == "instruction":
        target["instruction"] = "Changed output semantics"
    elif mutation == "input":
        target["max_input_tokens"] += 1
    elif mutation == "wider":
        target["max_output_tokens"] = raw["template"]["baseline"]["max_output_tokens"] + 1
    elif mutation == "key":
        target["key"] = raw["template"]["baseline"]["key"]
    else:
        raw["version"] = "cutback.improvement-campaign.v2"
    with pytest.raises(ValueError):
        ImprovementCampaign.model_validate(raw)


def test_compiler_reserves_every_static_target_fallback_and_one_untouched_confirmation():
    spec = portfolio()
    receipt = compile_campaign(spec, dataset(), Decimal("1"))
    targets = len(spec.static_portfolio)
    assert receipt["contract"] == "cutback.campaign-budget.v3"
    assert receipt["initial_validation_requests"] == 3 * (1 + targets)
    assert receipt["max_requests"] == 3 * (1 + targets) + 1 + 3 * 3 + 3 * 2
    assert receipt["strategy_order"][0] == "static_native_portfolio"
    assert len(receipt["static_portfolio"]) == targets
    raw = spec.model_dump(mode="json")
    raw["budget"]["max_requests"] = 3 * (1 + targets) + 3 * 2
    static_only = compile_campaign(ImprovementCampaign.model_validate(raw), dataset(), Decimal("1"))
    assert static_only["eligible"] and static_only["rounds"] == 0
    raw["budget"]["max_requests"] -= 1
    smaller = compile_campaign(ImprovementCampaign.model_validate(raw), dataset(), Decimal("1"))
    assert smaller["eligible"] and len(smaller["selected_static_keys"]) == targets - 1
    assert smaller["requested_static_count"] == targets
    assert len(smaller["omitted_static_keys"]) == 1
    assert smaller["split_samples"] == receipt["split_samples"]
    raw["budget"]["max_requests"] = 11
    assert not compile_campaign(ImprovementCampaign.model_validate(raw), dataset(), Decimal("1"))[
        "eligible"
    ]


def test_generation_can_use_each_exact_portfolio_seed_and_only_training_payloads():
    spec = portfolio()
    items = dataset()["items"]
    for index, row in enumerate(items):
        row["case"]["id"] = f"case-{index}"
        row["case"]["gold"] = "A"
        row["canonical_family_id"] = str(uuid4())
    text, families = generation_input(spec, items)
    scope = json.loads(text)
    assert len(families) == len(scope["training"]) == 3
    assert all("train" in row["input"] for row in scope["training"])
    assert {s["key"] for s in scope["seeds"]} == {
        spec.template.baseline.key,
        *(m.key for m in spec.static_portfolio),
    }
    assert generation_request_bytes(spec, text) <= generation_input_upper_bytes(
        spec, items, rounds=1, variants=2
    )
    for model in spec.static_portfolio:
        decoded = decode_proposals(
            json.dumps({"proposals": [proposal(seed_key=model.key)]}), spec, round_index=1
        )
        assert decoded[0].model == model.model
        assert decoded[0].request_contract == model.request_contract


def test_explicit_empty_portfolio_goes_to_generation_without_fictitious_static_spend():
    spec = portfolio(static_portfolio=[])
    receipt = compile_campaign(spec, dataset(), Decimal("1"))
    assert receipt["eligible"]
    assert receipt["initial_validation_requests"] == 0
    assert receipt["strategy_order"] == ["instruction_search_if_needed", "independent_confirmation"]


def test_portfolio_effort_and_money_ignore_application_decimal_traps():
    from decimal import Inexact, Rounded, localcontext

    spec = portfolio()
    expected = compile_campaign(spec, dataset(), Decimal("1"))
    with localcontext() as context:
        context.prec = 3
        context.traps[Inexact] = context.traps[Rounded] = True
        assert compile_campaign(spec, dataset(), Decimal("1")) == expected
