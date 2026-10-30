"""Explicit semantic-cache control and content contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.semantic_cache import SemanticCacheRule, unit_vector
from cutback.evals.semantic_cache import CacheQualificationSpec


class NamespaceInput(StrictModel):
    policy_id: UUID
    serving_key_id: UUID
    workflow: Identifier
    environment: Identifier
    provider: Literal["openai", "anthropic", "local"]
    model: Identifier
    prompt_revision: Identifier
    freshness_ref: Identifier
    access_context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule: SemanticCacheRule
    max_entries: int = Field(default=128, strict=True, ge=1, le=256)
    max_lookups_per_minute: int = Field(default=30, strict=True, ge=1, le=120)
    expires_at: datetime
    curated_catalog_approved: Literal[True]
    encrypted_storage_approved: Literal[True]

    @field_validator("curated_catalog_approved", "encrypted_storage_approved", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("SEMANTIC_CACHE_EXPLICIT_OWNER_APPROVAL_REQUIRED")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if value.tzinfo is None:
            raise ValueError("SEMANTIC_CACHE_TIMEZONE_REQUIRED")
        return value


class SemanticLookup(StrictModel):
    project_id: UUID
    namespace_id: UUID
    access_context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    embedding_contract_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    vector: tuple[float, ...] = Field(min_length=1, max_length=2048)

    @field_validator("vector", mode="before")
    @classmethod
    def finite_vector(cls, value):
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2048:
            raise ValueError("SEMANTIC_CACHE_VECTOR_BOUND")
        unit_vector(value, len(value))
        return value


class SemanticPut(SemanticLookup):
    query: str = Field(min_length=1, max_length=64000)
    response: str = Field(min_length=1, max_length=64000)
    source_group: Identifier | None = None
    ttl_seconds: int = Field(strict=True, ge=1, le=2592000)


class QualificationPrepare(StrictModel):
    dataset_id: UUID
    specification: CacheQualificationSpec
    max_wall_seconds: int = Field(default=60, strict=True, ge=1, le=120)


class SemanticApproval(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class SemanticControl(StrictModel):
    pass
