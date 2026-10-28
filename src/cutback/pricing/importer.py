"""Operator-driven catalog import; checksum required and immutable snapshots preserved."""

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from cutback.core.models import Identifier, StrictModel


class RateCard(StrictModel):
    provider: Literal["openai", "anthropic"]
    model: Identifier
    aliases: list[Identifier] = Field(max_length=100)
    input: Decimal = Field(ge=0, le=1000000, max_digits=24, decimal_places=12)
    output: Decimal = Field(ge=0, le=1000000, max_digits=24, decimal_places=12)
    cache_read: Decimal = Field(ge=0, le=1000000, max_digits=24, decimal_places=12)
    cache_write: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=24, decimal_places=12
    )
    cache_write_1h: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=24, decimal_places=12
    )
    input_token_contract: Literal["openai-inclusive-cache-writes-v2"] | None = None
    cache_write_30m: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=24, decimal_places=12
    )
    cache_write_12h: Decimal | None = Field(
        default=None, ge=0, le=1000000, max_digits=24, decimal_places=12
    )
    long_context_threshold: int | None = Field(default=None, strict=True, ge=1, le=100000000)
    long_context_input_multiplier: Decimal | None = Field(
        default=None, ge=1, le=10, max_digits=16, decimal_places=12
    )
    long_context_output_multiplier: Decimal | None = Field(
        default=None, ge=1, le=10, max_digits=16, decimal_places=12
    )
    effective_from: datetime
    source: str = Field(pattern=r"^https://", max_length=1000)

    @model_validator(mode="after")
    def usage_contract(self):
        modern = (
            self.cache_write_30m,
            self.cache_write_12h,
            self.long_context_threshold,
            self.long_context_input_multiplier,
            self.long_context_output_multiplier,
        )
        if self.input_token_contract is None:
            if any(value is not None for value in modern):
                raise ValueError("VERSIONED_INPUT_TOKEN_CONTRACT_REQUIRED")
        elif (
            self.provider != "openai"
            or any(value is None for value in modern)
            or self.cache_write is not None
            or self.cache_write_1h is not None
        ):
            raise ValueError("COMPLETE_DISJOINT_OPENAI_V2_RATE_CONTRACT_REQUIRED")
        return self


class CatalogSnapshot(StrictModel):
    schema_version: Literal["1.0", "1.1"]
    snapshot_id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]
    checked_at: date
    provenance: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(max_length=30)
    rates: list[RateCard] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def identities(self):
        seen = set()
        for rate in self.rates:
            if self.schema_version == "1.0" and rate.input_token_contract is not None:
                raise ValueError("MODERN_RATE_CONTRACT_REQUIRES_SNAPSHOT_SCHEMA_1_1")
            if rate.effective_from.tzinfo is None:
                raise ValueError("effective date requires UTC offset")
            for model in [rate.model, *rate.aliases]:
                key = (rate.provider, model)
                if key in seen:
                    raise ValueError("ambiguous model alias")
                seen.add(key)
        return self


def validate_snapshot(raw: bytes, expected_sha256: str):
    if len(raw) > 2097152 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("CATALOG_SIZE_OR_CHECKSUM_MISMATCH")
    return CatalogSnapshot.model_validate_json(raw)


def import_snapshot(raw: bytes, expected_sha256: str, destination: Path):
    snapshot = validate_snapshot(raw, expected_sha256)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / (snapshot.snapshot_id + ".json")
    if path.is_symlink():
        raise ValueError("UNSAFE_CATALOG_DESTINATION")
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("IMMUTABLE_CATALOG_VERSION_ALREADY_EXISTS")
    else:
        with path.open("xb") as stream:
            stream.write(raw)
    return {
        "snapshot_id": snapshot.snapshot_id,
        "sha256": expected_sha256,
        "path": str(path),
        "historical_valuations_changed": False,
        "activation": "set CUTBACK_PRICE_CATALOG and CUTBACK_PRICE_SHA256 explicitly",
    }
