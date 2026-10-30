"""Bounded cosine reuse over supplied embeddings; similarity never proves answer validity.

Pure in-memory computation. Callers authorize and decrypt their own namespace
before construction. This module never discovers keys, embeds text, reads files,
executes application code or sends a request.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from cutback.core.models import Identifier, StrictModel

RECIPE = {
    "version": "cutback.supplied-embedding-cache.v1",
    "matching": "normalized_dense_cosine_fsum_v1",
    "tie_break": "anchor_uuid_ascending",
    "ambiguity": "nearest_distinct_exact_response_margin_lte_refuses",
    "threshold": "score_gte_frozen_threshold",
    "embedding_origin": "externally_asserted_not_verified_by_similarity",
    "quality": "must_be_qualified_separately_against_task_references",
}
CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(RECIPE, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
MAX_ENTRIES = 256
MAX_DIMENSIONS = 2048
MAX_CONTENT_BYTES = 24 * 1048576


class EmbeddingContract(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    adapter: Identifier
    revision_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dimensions: int = Field(strict=True, ge=1, le=MAX_DIMENSIONS)


class SemanticCacheRule(StrictModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    version: Literal["cutback.supplied-embedding-cache.v1"] = "cutback.supplied-embedding-cache.v1"
    contract_sha256: str = Field(default=CONTRACT_SHA256, pattern=r"^[a-f0-9]{64}$")
    embedding: EmbeddingContract
    threshold: float = Field(ge=0, le=1)
    minimum_distinct_answer_margin: float = Field(default=0.01, ge=0, le=1)

    @field_validator("threshold", "minimum_distinct_answer_margin", mode="before")
    @classmethod
    def finite_threshold(cls, value):
        if type(value) not in {float, int} or not 0 <= value <= 1 or not math.isfinite(value):
            raise ValueError("SEMANTIC_CACHE_FINITE_NUMERIC_RULE_REQUIRED")
        return value

    @model_validator(mode="after")
    def current_contract(self):
        if self.contract_sha256 != CONTRACT_SHA256:
            raise ValueError("SEMANTIC_CACHE_CONTRACT_CHANGED_REQUALIFY")
        return self


def unit_vector(value, dimensions):
    if not isinstance(value, (tuple, list)) or len(value) != dimensions:
        raise ValueError("SEMANTIC_CACHE_EMBEDDING_DIMENSION_MISMATCH")
    if any(
        type(item) not in {float, int} or abs(item) > 1000000 or not math.isfinite(item)
        for item in value
    ):
        raise ValueError("SEMANTIC_CACHE_FINITE_BOUNDED_VECTOR_REQUIRED")
    magnitude = math.sqrt(math.fsum(item * item for item in value))
    if not magnitude or not math.isfinite(magnitude):
        raise ValueError("SEMANTIC_CACHE_NONZERO_VECTOR_REQUIRED")
    return tuple(item / magnitude for item in value)


@dataclass(frozen=True)
class CacheAnchor:
    identity: UUID
    vector: tuple[float, ...]
    response: str


@dataclass(frozen=True)
class CacheMatch:
    hit: bool
    code: str
    anchor_id: UUID | None = None
    score: float | None = None
    distinct_answer_score: float | None = None
    response: str | None = None


def select_match(rule, identity, score, rival, response):
    """Apply one frozen policy to already computed neighbors inside the authorized content plane."""
    if identity is None:
        return CacheMatch(False, "SEMANTIC_CACHE_EMPTY")
    if (
        not isinstance(identity, UUID)
        or identity.int == 0
        or type(score) not in {float, int}
        or not -1 <= score <= 1
        or not math.isfinite(score)
        or rival is not None
        and (type(rival) not in {float, int} or not -1 <= rival <= 1 or not math.isfinite(rival))
        or type(response) is not str
        or not response
    ):
        raise ValueError("SEMANTIC_CACHE_VALIDATED_NEIGHBORS_REQUIRED")
    if score < rule.threshold:
        return CacheMatch(False, "SEMANTIC_CACHE_BELOW_THRESHOLD", identity, score, rival)
    if rival is not None and score - rival <= rule.minimum_distinct_answer_margin:
        return CacheMatch(False, "SEMANTIC_CACHE_AMBIGUOUS_ANSWERS", identity, score, rival)
    return CacheMatch(
        True, "SEMANTIC_CACHE_SIMILARITY_MATCH_NOT_QUALITY_PROOF", identity, score, rival, response
    )


class SemanticCacheIndex:
    """Exhaustive bounded search, never a partial approximate index presented as complete."""

    def __init__(self, rule: SemanticCacheRule, anchors, *, check=None):
        if not isinstance(anchors, (list, tuple)) or len(anchors) > MAX_ENTRIES:
            raise ValueError("SEMANTIC_CACHE_INDEX_ENTRY_BOUND")
        self.rule, self.anchors, self.check = rule, [], check
        seen, size = set(), 0
        for anchor in anchors:
            self.authority()
            if (
                not isinstance(anchor, CacheAnchor)
                or not isinstance(anchor.identity, UUID)
                or anchor.identity.int == 0
                or anchor.identity in seen
                or type(anchor.response) is not str
                or not 1 <= len(anchor.response.encode()) <= 64000
            ):
                raise ValueError("SEMANTIC_CACHE_ANCHOR_IDENTITY_OR_CONTENT_INVALID")
            seen.add(anchor.identity)
            vector = unit_vector(anchor.vector, rule.embedding.dimensions)
            size += len(anchor.response.encode()) + len(vector) * 8
            if size > MAX_CONTENT_BYTES:
                raise ValueError("SEMANTIC_CACHE_INDEX_BYTE_BOUND")
            self.anchors.append(CacheAnchor(anchor.identity, vector, anchor.response))
        self.authority()

    def authority(self):
        if self.check:
            self.check()

    def lookup(self, vector):
        self.authority()
        query = unit_vector(vector, self.rule.embedding.dimensions)
        ranked = []
        for anchor in self.anchors:
            self.authority()
            score = max(
                -1.0, min(1.0, math.fsum(a * b for a, b in zip(query, anchor.vector, strict=True)))
            )
            ranked.append((score, anchor))
        ranked.sort(key=lambda item: (-item[0], item[1].identity.int))
        self.authority()
        if not ranked:
            return CacheMatch(False, "SEMANTIC_CACHE_EMPTY")
        score, winner = ranked[0]
        rival = next(
            (value for value, anchor in ranked[1:] if anchor.response != winner.response), None
        )
        self.authority()
        return select_match(self.rule, winner.identity, score, rival, winner.response)
