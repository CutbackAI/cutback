"""Finite, content-free gateway approval contracts; no provider calls or secrets.

Gateway spend means modeled USD under the frozen public price contract, not an
invoice cap. Reserve the complete supported context, rather than guessing an
application's input token count from character length or a local tokenizer.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BeforeValidator, Field, field_validator, model_validator

from cutback.core.models import StrictModel
from cutback.core.native_models import LunaEffort, NativeModelContract


def exact_money(value):
    if type(value) not in {Decimal, str, int}:
        raise ValueError("GATEWAY_EXACT_DECIMAL_MONEY_REQUIRED")
    return value


Money = Annotated[
    Decimal, BeforeValidator(exact_money), Field(gt=0, lt=Decimal("1000000000"), decimal_places=12)
]

# Independent execution facts checked against the official model page on
# 2026-10-28. These do not edit a released observation price snapshot.
# https://developers.openai.com/api/docs/models/gpt-6-luna
LUNA_CONTEXT_TOKENS = 1_050_000


def aware(value: datetime):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("GATEWAY_UTC_OFFSET_REQUIRED")
    return value.astimezone(UTC)


class SpendLimit(StrictModel):
    modeled_usd: Money
    requests: int = Field(strict=True, ge=1, le=100000)
    concurrent_requests: int = Field(strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def concurrency_within_total(self):
        if self.concurrent_requests > self.requests:
            raise ValueError("GATEWAY_CONCURRENCY_EXCEEDS_REQUEST_LIMIT")
        return self


class TeamLimit(StrictModel):
    team_id: UUID
    limit: SpendLimit


class ModelLimit(StrictModel):
    # An exact model and route, not an arbitrary destination or alias resolver.
    key: Literal["openai-luna-responses-text-v1", "openai-luna-responses-text-stream-v1"] = (
        "openai-luna-responses-text-v1"
    )
    provider: Literal["openai"] = "openai"
    model: Literal["gpt-6-luna"] = "gpt-6-luna"
    sdk_surface: Literal["responses.create"] = "responses.create"
    reservation_contract: Literal["openai-luna-full-context-fast-reserve-v1"] = (
        "openai-luna-full-context-fast-reserve-v1"
    )
    context_tokens: Literal[1050000] = 1_050_000
    price_contract: NativeModelContract
    max_output_tokens: int = Field(default=1024, strict=True, ge=16, le=16384)
    timeout_seconds: int = Field(default=60, strict=True, ge=1, le=120)
    limit: SpendLimit

    @model_validator(mode="after")
    def standard_requested(self):
        if self.price_contract.service_tier != "default":
            raise ValueError("GATEWAY_EXPLICIT_DEFAULT_TIER_REQUIRED")
        return self


class GatewayPolicy(StrictModel):
    version: Literal["cutback.gateway-policy.v1", "cutback.gateway-policy.v2"] = (
        "cutback.gateway-policy.v1"
    )
    policy_id: UUID
    environment_ids: list[UUID] = Field(min_length=1, max_length=100)
    starts_at: datetime
    expires_at: datetime
    project_limit: SpendLimit
    teams: list[TeamLimit] = Field(min_length=1, max_length=100)
    models: list[ModelLimit] = Field(min_length=1, max_length=5)

    _times = field_validator("starts_at", "expires_at")(aware)

    @model_validator(mode="after")
    def finite_unique_scopes(self):
        expected = (
            "openai-luna-responses-text-v1"
            if self.version == "cutback.gateway-policy.v1"
            else "openai-luna-responses-text-stream-v1"
        )
        if any(model.key != expected for model in self.models):
            raise ValueError("GATEWAY_POLICY_VERSION_MODEL_CONTRACT_MISMATCH")
        if not timedelta(0) < self.expires_at - self.starts_at <= timedelta(days=31):
            raise ValueError("GATEWAY_FINITE_WINDOW_REQUIRED")
        identities: list[list[str]] = [
            [str(value) for value in self.environment_ids],
            [str(row.team_id) for row in self.teams],
            [row.key for row in self.models],
        ]
        if any(len(set(values)) != len(values) for values in identities):
            raise ValueError("GATEWAY_DUPLICATE_POLICY_SCOPE")
        if (
            any(value.int == 0 for value in self.environment_ids)
            or any(row.team_id.int == 0 for row in self.teams)
            or self.policy_id.int == 0
        ):
            raise ValueError("GATEWAY_NONZERO_IDENTITY_REQUIRED")
        for limit in [row.limit for row in self.teams] + [row.limit for row in self.models]:
            if (
                limit.modeled_usd > self.project_limit.modeled_usd
                or limit.requests > self.project_limit.requests
                or limit.concurrent_requests > self.project_limit.concurrent_requests
            ):
                raise ValueError("GATEWAY_CHILD_LIMIT_EXCEEDS_PROJECT")
        return self

    def approval_hash(self):
        raw = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(raw).hexdigest()


class GatewayKeyInput(StrictModel):
    request_id: UUID
    policy_id: UUID
    environment_id: UUID
    team_id: UUID
    traffic_kind: Literal[
        "application", "evaluation", "judge", "optimization", "simulation", "unknown"
    ] = "application"
    expires_at: datetime
    limit: SpendLimit
    model_keys: list[
        Literal["openai-luna-responses-text-v1", "openai-luna-responses-text-stream-v1"]
    ] = Field(min_length=1, max_length=5)

    _time = field_validator("expires_at")(aware)

    @model_validator(mode="after")
    def unique_nonzero_identity(self):
        if len(set(self.model_keys)) != len(self.model_keys) or any(
            value.int == 0
            for value in (self.request_id, self.policy_id, self.environment_id, self.team_id)
        ):
            raise ValueError("GATEWAY_KEY_SCOPE_INVALID")
        return self


class Reasoning(StrictModel):
    effort: LunaEffort = "medium"


class CacheOptions(StrictModel):
    ttl: Literal["30m"] = "30m"


class ResponsesText(StrictModel):
    """Bounded native wire contract. Arbitrary tools/content types are not accepted."""

    model: Literal["gpt-6-luna"]
    input: str = Field(min_length=1, max_length=262144)
    instructions: str | None = Field(default=None, max_length=262144)
    max_output_tokens: int = Field(strict=True, ge=16, le=16384)
    # Required: do not silently change the provider's default storage/tier behavior.
    store: Literal[False]
    service_tier: Literal["default"]
    stream: Literal[False] = False
    reasoning: Reasoning = Field(default_factory=Reasoning)
    prompt_cache_options: CacheOptions | None = None

    @field_validator("store", "stream", mode="before")
    @classmethod
    def actual_false(cls, value):
        if value is not False:
            raise ValueError("GATEWAY_FALSE_BOOLEAN_REQUIRED")
        return value


class StreamOptions(StrictModel):
    include_obfuscation: bool = Field(strict=True)


class ResponsesStream(StrictModel):
    """Explicit HTTP SSE text surface; legacy ResponsesText stays unchanged."""

    model: Literal["gpt-6-luna"]
    input: str = Field(min_length=1, max_length=262144)
    instructions: str | None = Field(default=None, max_length=262144)
    max_output_tokens: int = Field(strict=True, ge=16, le=16384)
    store: Literal[False]
    service_tier: Literal["default"]
    stream: Literal[True]
    reasoning: Reasoning = Field(default_factory=Reasoning)
    prompt_cache_options: CacheOptions | None = None
    stream_options: StreamOptions | None = None

    @field_validator("store", "stream", mode="before")
    @classmethod
    def actual_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("GATEWAY_ACTUAL_BOOLEAN_REQUIRED")
        return value


def qualify_model(model: ModelLimit, catalog=None):
    if catalog is None:
        from cutback.pricing import Catalog

        catalog = Catalog()
    reference = NativeModelContract.current_luna_text(
        catalog,
        reasoning_effort=model.price_contract.reasoning_effort,
        service_tier=model.price_contract.service_tier,
    )
    if model.price_contract != reference:
        raise ValueError("GATEWAY_PRICE_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
    information = catalog.rate_info(model.provider, model.model)
    if (
        information["snapshot_id"] != reference.price_snapshot_id
        or information["rate"] is None
        or information["rate"].get("input_token_contract") != "openai-inclusive-cache-writes-v2"
    ):
        raise ValueError("GATEWAY_MODEL_PRICE_SCOPE_UNQUALIFIED")
    return information["rate"]


def upper_cost(model: ModelLimit, catalog=None):
    rate = qualify_model(model, catalog)
    with localcontext() as context:
        context.prec = 80
        input_rate = max(
            Decimal(rate[key])
            for key in ("input", "cache_read", "cache_write_30m", "cache_write_12h")
        )
        input_rate *= Decimal(rate["long_context_input_multiplier"])
        output_rate = Decimal(rate["output"]) * Decimal(rate["long_context_output_multiplier"])
        # Reserve even the documented Fast premium. Only qualified actual
        # Standard usage can settle to known modeled cost in this first contract.
        return (
            (Decimal(model.context_tokens) * input_rate + model.max_output_tokens * output_rate)
            * 2
            / Decimal(1000000)
        )


def qualify_request(request: ResponsesText | ResponsesStream, model: ModelLimit):
    qualify_model(model)
    if (
        request.model != model.model
        or request.stream
        and model.key != "openai-luna-responses-text-stream-v1"
        or request.max_output_tokens > model.max_output_tokens
        or request.reasoning.effort != model.price_contract.reasoning_effort
    ):
        raise ValueError("GATEWAY_REQUEST_OUTSIDE_APPROVED_MODEL_CONTRACT")
