"""Real vector matching, ambiguity, bounds and authority; no inference quality claim."""

from uuid import UUID

import pytest

from cutback.core.semantic_cache import CacheAnchor, SemanticCacheIndex, SemanticCacheRule


def rule(**values):
    return SemanticCacheRule.model_validate(
        {
            "embedding": {
                "adapter": "owned-synthetic-vectors",
                "revision_sha256": "a" * 64,
                "dimensions": 2,
            },
            "threshold": 0.98,
            **values,
        }
    )


def test_real_cosine_reuses_nonidentical_vectors_but_misses_never_expose_response():
    index = SemanticCacheIndex(rule(), [CacheAnchor(UUID(int=1), (1.0, 0.0), "billing")])
    match = index.lookup((0.999, 0.01))
    assert match.hit and match.response == "billing" and 0.99 < match.score < 1
    miss = index.lookup((0.0, 1.0))
    assert not miss.hit and miss.response is None
    assert not SemanticCacheIndex(rule(), []).lookup((1.0, 0.0)).hit


def test_conflicting_answers_ties_and_margin_fail_closed_while_duplicate_answers_are_consistent():
    anchors = [
        CacheAnchor(UUID(int=2), (1.0, 0.0), "access"),
        CacheAnchor(UUID(int=1), (1.0, 0.0), "billing"),
    ]
    match = SemanticCacheIndex(rule(minimum_distinct_answer_margin=0), anchors).lookup((1.0, 0.0))
    assert not match.hit and match.response is None and match.anchor_id == UUID(int=1)
    duplicate = [CacheAnchor(UUID(int=3), (0.999, 0.01), "billing"), anchors[1]]
    assert SemanticCacheIndex(rule(), duplicate).lookup((1.0, 0.0)).hit


@pytest.mark.parametrize(
    "vector",
    [
        (True, 0),
        (float("nan"), 0),
        (float("inf"), 0),
        (0, 0),
        (1e100, 0),
        (1,),
        (1e-200, 0),
        ("1", 0),
    ],
)
def test_invalid_query_or_anchor_vector_never_partially_matches(vector):
    anchor = CacheAnchor(UUID(int=1), (1.0, 0.0), "owned")
    with pytest.raises(ValueError, match="SEMANTIC_CACHE_"):
        SemanticCacheIndex(rule(), [anchor]).lookup(vector)
    with pytest.raises(ValueError, match="SEMANTIC_CACHE_"):
        SemanticCacheIndex(rule(), [CacheAnchor(UUID(int=1), vector, "owned")])


def test_duplicate_identity_unbounded_index_and_expired_authority_are_refused():
    anchor = CacheAnchor(UUID(int=1), (1.0, 0.0), "owned")
    for anchors in [[anchor, anchor], [anchor] * 257]:
        with pytest.raises(ValueError, match="SEMANTIC_CACHE_"):
            SemanticCacheIndex(rule(), anchors)
    calls = []

    def check():
        calls.append(True)
        if len(calls) > 3:
            raise ValueError("OWNED_AUTHORITY_EXPIRED")

    index = SemanticCacheIndex(rule(), [anchor], check=check)
    with pytest.raises(ValueError, match="OWNED_AUTHORITY_EXPIRED"):
        index.lookup((1.0, 0.0))


@pytest.mark.parametrize("value", [True, "0.9", float("nan"), float("inf"), -0.01, 1.01])
def test_thresholds_are_explicit_finite_numbers(value):
    with pytest.raises(ValueError):
        rule(threshold=value)
