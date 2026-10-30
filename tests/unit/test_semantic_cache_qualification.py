"""Calibration cannot certify a high-hit but harmful cache or use confirmation to retune."""

import hashlib
from uuid import UUID

import pytest

from cutback.core.semantic_cache import CacheAnchor, SemanticCacheIndex
from cutback.evals.semantic_cache import (
    CacheQualificationQuery,
    CacheQualificationSpec,
    assess,
    calibrate,
    confirm,
    validate_queries,
)


def fixture():
    spec = CacheQualificationSpec.model_validate(
        {
            "rule": {
                "embedding": {
                    "adapter": "owned-synthetic",
                    "revision_sha256": "a" * 64,
                    "dimensions": 2,
                },
                "threshold": 0.5,
            },
            "thresholds": [0.5, 0.99],
            "maximum_false_positive_group_upper95": 0.1,
            "minimum_useful_hit_group_ratio": 0.1,
        }
    )
    anchors = [CacheAnchor(UUID(int=1), (1.0, 0.0), "billing")]
    cases = []
    for split in ["validation", "test"]:
        for index in range(100):
            identity = len(cases) + 1
            positive = index < 50
            cases.append(
                CacheQualificationQuery(
                    id=UUID(int=identity),
                    query=f"Owned {split} query {index}",
                    vector=(1.0, 0.0) if positive else (0.8, 0.6),
                    gold="billing" if positive else "access",
                    group_sha256=hashlib.sha256(f"family-{identity}".encode()).hexdigest(),
                    split=split,
                    baseline_status="completed",
                    baseline_output="billing" if positive else "access",
                )
            )
    return spec, anchors, cases


def test_low_threshold_false_hits_fail_then_one_validation_selected_threshold_is_confirmed():
    spec, anchors, cases = fixture()
    result = calibrate(spec, anchors, cases)
    assert result["selected_threshold"] == 0.99 and result["test_evaluated"] is False
    low = result["trials"][0]["assessment"]
    assert low["false_positive"] == low["true_positive"] == 50
    assert low["passed"] is False and low["hit_ratio"] == 1
    assert low["group_weighted_utility_delta"] == -0.5
    accepted = confirm(spec, anchors, cases, result["selected_threshold"])
    assert accepted["passed"] and accepted["hit_ratio"] == 0.5
    assert accepted["false_positive_group_upper95"] < 0.1
    assert accepted["complete_task_cost_usd"] is accepted["realized_savings_usd"] is None
    assert "Owned" not in str(result) and "billing" not in str(accepted)


def test_confirmation_regression_rejects_the_selection_without_searching_again():
    spec, anchors, cases = fixture()
    result = calibrate(spec, anchors, cases)
    changed = [
        case.model_copy(update={"vector": (1.0, 0.0)})
        if case.split == "test" and case.gold == "access"
        else case
        for case in cases
    ]
    assert calibrate(spec, anchors, changed) == result
    confirmation = confirm(spec, anchors, changed, result["selected_threshold"])
    assert confirmation["passed"] is False and confirmation["false_positive"] == 50
    with pytest.raises(ValueError, match="FROZEN_SELECTED_THRESHOLD"):
        confirm(spec, anchors, changed, 0.77)


def test_unknown_fallback_quality_never_becomes_a_passing_utility_or_cost_claim():
    spec, anchors, cases = fixture()
    cases = [
        case.model_copy(update={"baseline_status": "unknown", "baseline_output": None})
        for case in cases
    ]
    report = assess(SemanticCacheIndex(spec.rule, anchors), cases[:100], spec)
    assert report["baseline_quality"] is report["group_weighted_utility_delta"] is None
    assert report["passed"] is False


def test_duplicate_queries_vector_noise_and_cross_split_family_reuse_are_refused():
    spec, _, cases = fixture()
    for update, code in [
        ({"query": cases[0].query, "vector": (0.99, 0.01)}, "DUPLICATE_QUERY"),
        ({"group_sha256": cases[0].group_sha256}, "DISJOINT_CALIBRATION"),
    ]:
        changed = [*cases[:-1], cases[-1].model_copy(update=update)]
        with pytest.raises(ValueError, match=code):
            validate_queries(spec, changed)


def test_no_hits_or_only_positive_examples_cannot_certify_a_useful_safe_cache():
    spec, anchors, cases = fixture()
    empty = calibrate(spec, [], cases)
    assert empty["selected_threshold"] is None
    positives = [case for case in cases if case.gold == "billing"]
    result = calibrate(spec, anchors, positives)
    assert result["selected_threshold"] is None
    assert result["trials"][0]["assessment"]["negative_control_groups"] == 0


def test_threshold_grid_reuses_neighbors_instead_of_repeating_dense_search(monkeypatch):
    spec, anchors, cases = fixture()
    spec = spec.model_copy(update={"thresholds": tuple(index / 31 for index in range(32))})
    original = SemanticCacheIndex.lookup
    calls = []

    def observed(self, vector):
        calls.append(vector)
        return original(self, vector)

    monkeypatch.setattr(SemanticCacheIndex, "lookup", observed)
    report = calibrate(spec, anchors, cases)
    assert len(report["trials"]) == 32
    assert len(calls) == sum(case.split == "validation" for case in cases)


@pytest.mark.parametrize("value", [True, "0.5", 10**1000, float("nan")])
def test_rule_and_query_numeric_validation_does_not_coerce_or_overflow(value):
    spec, _, cases = fixture()
    with pytest.raises(ValueError):
        CacheQualificationSpec.model_validate({**spec.model_dump(), "thresholds": [value]})
    with pytest.raises(ValueError):
        CacheQualificationQuery.model_validate({**cases[0].model_dump(), "vector": [value, 0]})
