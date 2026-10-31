"""Explicit experiment cache authority and declared infrastructure cost bounds."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from cutback.core.models import Identifier, StrictModel

EXACT_CACHE_CONTRACT: Literal["native-exact-cache-v1"] = "native-exact-cache-v1"


class ExactCacheExecution(StrictModel):
    version: Literal["native-exact-cache-v1"] = "native-exact-cache-v1"
    freshness_ref: Annotated[Identifier, Field(max_length=100)]
    ttl_seconds: int = Field(strict=True, ge=1, le=2592000)
    content_retention_authorized: Literal[True]
    immutable_or_safely_invalidated: Literal[True]
    # This is an approved bound, not a measured infrastructure bill. It is
    # charged conservatively for every lookup and every attempted write.
    operation_cost_upper_usd: Decimal = Field(ge=0, le=100, decimal_places=12)
    operation_cost_basis_ref: Identifier
    namespace: Literal["isolated_experiment_run"] = "isolated_experiment_run"

    @field_validator(
        "content_retention_authorized", "immutable_or_safely_invalidated", mode="before"
    )
    @classmethod
    def explicit_authority(cls, value):
        if value is not True:
            raise ValueError("EXACT_CACHE_EXPLICIT_BOOLEAN_AUTHORITY_REQUIRED")
        return value

    @field_validator("operation_cost_upper_usd", mode="before")
    @classmethod
    def decimal_money(cls, value):
        if type(value) not in {str, int, Decimal}:
            raise ValueError("EXACT_CACHE_COST_REQUIRES_EXACT_DECIMAL_INPUT")
        return value


class ExactCacheObservation(StrictModel):
    origin: Literal["native_provider", "encrypted_cache"]
    native_sdk_calls: int = Field(strict=True, ge=0, le=1)
    cache_write: Literal[
        "not_attempted", "created", "conflict", "rejected", "uncertain", "not_eligible"
    ]
    cache_operations: int = Field(strict=True, ge=1, le=2)
    provider_modeled_cost_usd: Decimal | None = Field(ge=0, le=100)
    cache_operation_cost_upper_usd: Decimal = Field(ge=0, le=200)
    cache_operation_cost_basis_ref: Identifier
    invoice_verified_cost_usd: None = None
    historical_response_model: Identifier | None = None

    @model_validator(mode="after")
    def coherent(self):
        hit = self.origin == "encrypted_cache"
        writes = self.cache_write not in {"not_attempted", "not_eligible"}
        if (
            self.native_sdk_calls != int(not hit)
            or self.cache_operations != 1 + int(writes)
            or hit
            and (self.cache_write != "not_attempted" or self.provider_modeled_cost_usd != 0)
            or not hit
            and self.cache_write == "not_attempted"
            or not hit
            and self.historical_response_model is not None
        ):
            raise ValueError("EXACT_CACHE_OBSERVATION_INCONSISTENT")
        return self


def summarize_cache(observations, expected):
    values = [ExactCacheObservation.model_validate(item) for item in observations]
    hits = sum(item.origin == "encrypted_cache" for item in values)
    writes = {
        name: sum(item.cache_write == name for item in values)
        for name in ("created", "conflict", "rejected", "uncertain", "not_eligible")
    }
    return {
        "version": "native-exact-cache-v1",
        "logical_requests": len(values),
        "native_sdk_calls": sum(item.native_sdk_calls for item in values),
        "cache_hits": hits,
        "cache_operations": sum(item.cache_operations for item in values),
        "cache_writes": writes,
        "execution_qualified": len(values) == expected
        and hits > 0
        and not any(writes[name] for name in ("conflict", "rejected", "uncertain", "not_eligible")),
        "cache_operation_cost_upper_usd": str(
            sum((item.cache_operation_cost_upper_usd for item in values), Decimal(0))
        ),
        "cost_basis": "provider_modeled_usage_plus_declared_cache_operation_upper_bound",
        "historical_cached_usage_priced_again": False,
        "namespace": "isolated_experiment_run",
        "production_hit_rate": None,
        "invoice_verified_cost_usd": None,
        "whole_task_cost_usd": None,
    }
