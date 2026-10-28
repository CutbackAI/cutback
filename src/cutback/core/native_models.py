"""Explicit model/request/price references for newly qualified native execution.

This is plan metadata, not spend authorization. Released plans omit this extension.
"""

from typing import Literal

from pydantic import Field

from cutback.core.models import StrictModel

LunaEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
LunaTier = Literal["default", "auto"]


class NativeModelContract(StrictModel):
    version: Literal["openai-luna-responses-text-v1"] = "openai-luna-responses-text-v1"
    price_snapshot_id: Literal["openai-text-2026-10-28-v1"] = "openai-text-2026-10-28-v1"
    price_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reasoning_effort: LunaEffort = "none"
    service_tier: LunaTier = "default"
    cache_write_lifetime: Literal["30m"] = "30m"

    @classmethod
    def current_luna_text(
        cls,
        catalog=None,
        *,
        reasoning_effort: LunaEffort = "none",
        service_tier: LunaTier = "default",
    ):
        if (
            type(reasoning_effort) is not str
            or reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}
            or type(service_tier) is not str
            or service_tier not in {"auto", "default"}
        ):
            raise ValueError("MODERN_NATIVE_REQUEST_OPTIONS_UNQUALIFIED")
        # Lazy and core-only: neither profile preparation nor scanning imports an SDK.
        if catalog is None:
            from cutback.pricing import Catalog

            catalog = Catalog()
        addition = catalog.observation_addition()
        if addition is None or addition[0]["snapshot_id"] != "openai-text-2026-10-28-v1":
            raise ValueError("MODERN_NATIVE_PRICE_SNAPSHOT_UNAVAILABLE")
        return cls(
            price_sha256=addition[1], reasoning_effort=reasoning_effort, service_tier=service_tier
        )


def validate_luna_text_contract(model, catalog):
    reference = model.native_contract
    if not isinstance(reference, NativeModelContract):
        raise ValueError("MODERN_NATIVE_CONTRACT_REFERENCE_REQUIRED")
    if reference != NativeModelContract.current_luna_text(
        catalog, reasoning_effort=reference.reasoning_effort, service_tier=reference.service_tier
    ):
        raise ValueError("MODERN_NATIVE_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
    if model.provider != "openai" or model.model != "gpt-6-luna" or model.region is not None:
        raise ValueError("MODERN_NATIVE_MODEL_SCOPE_MISMATCH")
    information = catalog.rate_info(model.provider, model.model)
    if (
        information["snapshot_id"] != reference.price_snapshot_id
        or information["rate"] is None
        or information["rate"].get("input_token_contract") != "openai-inclusive-cache-writes-v2"
    ):
        raise ValueError("MODERN_NATIVE_PRICE_SCOPE_MISMATCH")
    if (
        type(model.max_input_tokens) is not int
        or not 1 <= model.max_input_tokens <= 100000
        or type(model.max_output_tokens) is not int
        or not 16 <= model.max_output_tokens <= 16384
    ):
        raise ValueError("MODERN_NATIVE_TOKEN_BOUND_UNQUALIFIED")
    if model.agent_turn is not None or model.prompt_cache is not None:
        raise ValueError("MODERN_NATIVE_TEXT_ONLY_CONTRACT")
    contract = model.request_contract
    if contract is not None:
        if contract.sdk_surface != "responses.create":
            raise ValueError("MODERN_NATIVE_RESPONSES_SURFACE_REQUIRED")
        if set(contract.options) - {"text", "reasoning", "service_tier"}:
            raise ValueError("MODERN_NATIVE_REQUEST_OPTIONS_UNQUALIFIED")
        # Preserve explicit choices and freeze the documented medium/auto defaults.
        # Never substitute a nonreasoning or Standard-only baseline without evidence.
        if contract.options.get("reasoning", {"effort": "medium"}) != {
            "effort": reference.reasoning_effort
        } or (contract.options.get("service_tier", "auto") != reference.service_tier):
            raise ValueError("MODERN_NATIVE_SOURCE_REASONING_OR_TIER_MISMATCH")
    return reference
