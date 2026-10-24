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
    input: Decimal = Field(ge=0, le=1000000)
    output: Decimal = Field(ge=0, le=1000000)
    cache_read: Decimal = Field(ge=0, le=1000000)
    cache_write: Decimal | None = Field(default=None, ge=0, le=1000000)
    cache_write_1h: Decimal | None = Field(default=None, ge=0, le=1000000)
    effective_from: datetime
    source: str = Field(pattern=r"^https://", max_length=1000)


class CatalogSnapshot(StrictModel):
    schema_version: Literal["1.0"]
    snapshot_id: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]
    checked_at: date
    provenance: str = Field(min_length=1, max_length=2000)
    limitations: list[str] = Field(max_length=30)
    rates: list[RateCard] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def identities(self):
        seen = set()
        for rate in self.rates:
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
