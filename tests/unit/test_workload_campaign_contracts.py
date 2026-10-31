"""Measured cache eligibility stays a fixed control, not a prompt-generation seed."""

import json
from decimal import Decimal
from uuid import uuid4

import pytest
from test_campaign_budget import dataset
from test_campaign_contracts import campaign, proposal
from unit.test_workload_portfolio import plan  # noqa: F401 -- shared owned source fixture

from cutback.evals.campaign_budget import compile_campaign
from cutback.evals.campaigns import (
    ImprovementCampaign,
    automatic_static_portfolio,
    decode_proposals,
    generated_validation_controls,
    generation_input,
    generation_seeds,
)
from cutback.evals.experiments import ExperimentPlan


@pytest.fixture
def workload_campaign(request):
    source = request.getfixturevalue("plan")
    generator = campaign().generator.model_dump(mode="json")
    generator["model"]["max_input_tokens"] = 24000
    generator["max_output_tokens"] = source.baseline.max_output_tokens
    return campaign(
        version="cutback.improvement-campaign.v4",
        template=source.model_dump(mode="json"),
        static_portfolio=[m.model_dump(mode="json") for m in automatic_static_portfolio(source)],
        generator=generator,
        conservative_savings_per_success_usd="0.1",
        forecast_successful_tasks=1000,
    )


def test_frozen_cache_control_is_first_and_never_a_generation_seed(workload_campaign):
    spec = workload_campaign
    cache = spec.template.candidates[0]
    assert spec.static_portfolio[0] == cache
    assert cache not in generation_seeds(spec)
    assert generated_validation_controls(spec) == [cache]
    assert ImprovementCampaign.model_validate(spec.model_dump(mode="json")) == spec
    items = dataset()["items"]
    for index, item in enumerate(items):
        item["case"].update(id=f"owned-{index}", gold="A")
        item["canonical_family_id"] = str(uuid4())
    text, _ = generation_input(spec, items)
    assert cache.key not in {row["key"] for row in json.loads(text)["seeds"]}
    with pytest.raises(ValueError):
        decode_proposals(
            json.dumps({"proposals": [proposal(seed_key=cache.key)]}), spec, round_index=1
        )


def test_prompt_validation_keeps_the_qualified_unchanged_cache_control(workload_campaign):
    spec = workload_campaign
    proposed = decode_proposals(
        json.dumps(
            {"proposals": [proposal(seed_key=spec.template.baseline.key, max_output_tokens=32)]}
        ),
        spec,
        round_index=1,
    )
    raw = spec.template.model_dump(mode="json")
    raw["candidates"] = [
        m.model_dump(mode="json") for m in [*generated_validation_controls(spec), *proposed]
    ]
    derived = ExperimentPlan.model_validate(raw)
    assert derived.candidates[0] == spec.template.candidates[0]
    assert derived.candidates[1].exact_cache is None
    assert derived.candidates[1].instruction != derived.baseline.instruction
    assert derived.workload_portfolio == spec.template.workload_portfolio


def test_compilation_reserves_repeated_cache_control_and_retains_it_under_narrowing(
    workload_campaign,
):
    spec = workload_campaign
    result = compile_campaign(spec, dataset(), Decimal("1"))
    assert result["contract"] == "cutback.campaign-budget.v4" and result["eligible"]
    assert result["max_requests"] == 3 * (1 + len(spec.static_portfolio)) + 1 + 3 * 4 + 3 * 2
    assert result["strategy_order"][0] == "workload_qualified_native_cache_portfolio"
    assert result["static_portfolio"][0]["engine"] == "exact_cache"
    raw = spec.model_dump(mode="json")
    raw["budget"]["max_requests"] = 12
    narrowed = compile_campaign(ImprovementCampaign.model_validate(raw), dataset(), Decimal("1"))
    assert narrowed["eligible"] and narrowed["rounds"] == 0
    assert narrowed["selected_static_keys"] == [spec.template.candidates[0].key]
    assert narrowed["split_samples"] == result["split_samples"]
    raw["budget"]["max_requests"] = 11
    assert not compile_campaign(ImprovementCampaign.model_validate(raw), dataset(), Decimal("1"))[
        "eligible"
    ]


@pytest.mark.parametrize("change", ["legacy", "missing", "order", "cost", "source"])
def test_cache_control_cannot_be_removed_mutated_or_smuggled_into_legacy_campaigns(
    workload_campaign, change
):
    raw = workload_campaign.model_dump(mode="json")
    if change == "legacy":
        raw["version"] = "cutback.improvement-campaign.v3"
    elif change == "missing":
        raw["static_portfolio"] = raw["static_portfolio"][1:]
    elif change == "order":
        raw["static_portfolio"].reverse()
    elif change == "cost":
        raw["static_portfolio"][0]["exact_cache"]["operation_cost_upper_usd"] = "0.0011"
    else:
        raw["template"].pop("workload_portfolio")
    with pytest.raises(ValueError):
        ImprovementCampaign.model_validate(raw)
