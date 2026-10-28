"""Immutable opt-in model contracts. No provider request or SDK import is needed."""

from decimal import Decimal

import pytest

from cutback.core.native_models import NativeModelContract
from cutback.core.request_profiles import RequestContract
from cutback.evals.experiments import ModelSpec, qualified_native_model, upper_cost


def luna(**updates):
    return ModelSpec(
        key="owned",
        provider="openai",
        model="gpt-6-luna",
        native_contract=NativeModelContract.current_luna_text(),
        **updates,
    )


def test_new_native_model_requires_explicit_price_bound_contract_and_reserves_cache_writes():
    with pytest.raises(ValueError, match="MODEL_CAPABILITY_CONTRACT_NOT_VALIDATED"):
        qualified_native_model(ModelSpec(key="owned", provider="openai", model="gpt-6-luna"))
    value = luna(max_input_tokens=10000, max_output_tokens=100)
    qualified_native_model(value)
    assert upper_cost(value) == Decimal("0.00255")
    assert "native_contract" in value.model_dump(mode="json")
    legacy = ModelSpec(key="owned", provider="openai", model="gpt-4.1-mini")
    assert "native_contract" not in legacy.model_dump(mode="json")


@pytest.mark.parametrize("damage", ["price", "model", "provider", "surface", "reasoning", "tier"])
def test_tampered_or_incompatible_contract_cannot_authorize_native_execution(damage):
    value = luna()
    if damage == "price":
        value = value.model_copy(
            update={
                "native_contract": value.native_contract.model_copy(
                    update={"price_sha256": "0" * 64}
                )
            }
        )
    elif damage in {"model", "provider"}:
        value = value.model_copy(
            update={damage: "gpt-4.1-mini" if damage == "model" else "anthropic"}
        )
    else:
        options = {"reasoning": {"effort": "none"}, "service_tier": "default"}
        if damage == "reasoning":
            options["reasoning"] = {"effort": "high"}
        elif damage == "tier":
            options["service_tier"] = "auto"
        value = value.model_copy(
            update={
                "request_contract": RequestContract(
                    sdk_surface="chat.completions.create"
                    if damage == "surface"
                    else "responses.create",
                    payload={"op": "literal", "value": "synthetic"},
                    parameters=[],
                    options={} if damage == "surface" else options,
                )
            }
        )
    with pytest.raises(ValueError, match="MODERN_NATIVE"):
        qualified_native_model(value)


@pytest.mark.parametrize("effort,tier", [(None, None), ("none", "default"), ("max", "auto")])
def test_parse_only_source_preparation_freezes_declared_or_documented_defaults(
    tmp_path, effort, tier
):
    from test_request_profiles import SOURCE, inspect

    options = (
        "" if effort is None else f', reasoning={{"effort":"{effort}"}}, service_tier="{tier}"'
    )
    source = SOURCE.replace('model="gpt-4.1"', 'model="gpt-6-luna"' + options)
    _, _, profile = inspect(tmp_path, source)
    baseline = ModelSpec.model_validate(profile.baseline())
    qualified_native_model(baseline)
    assert baseline.native_contract.reasoning_effort == (effort or "medium")
    assert baseline.native_contract.service_tier == (tier or "auto")
    assert baseline.request_contract.model_dump() == profile.request.model_dump()
    assert (
        baseline.native_contract.price_sha256
        == NativeModelContract.current_luna_text().price_sha256
    )


def test_text_contract_cannot_be_approved_as_an_agent_episode_target():
    from test_episode_runner import episode_plan

    from cutback.evals.experiments import ExperimentPlan

    specification = episode_plan().model_dump(mode="json")
    specification["baseline"] = (
        luna().model_copy(update={"key": "baseline"}).model_dump(mode="json")
    )
    with pytest.raises(ValueError, match="MODERN_NATIVE_TEXT_CONTRACT_CANNOT_EXECUTE_EPISODES"):
        ExperimentPlan.model_validate(specification)
