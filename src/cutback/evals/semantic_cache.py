"""Task qualification for supplied-vector caches; no embedding or inference request.

Calibration picks one threshold before confirmation. Results describe supplied
references/vectors, not independently verified embedding provenance or real savings.
"""

import math
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from cutback.core.models import StrictModel
from cutback.core.semantic_cache import (
    SemanticCacheIndex,
    SemanticCacheRule,
    select_match,
    unit_vector,
)
from cutback.evals.datasets import digest
from cutback.evals.metrics import MetricSpec, evaluate, wilson


class CacheQualificationQuery(StrictModel):
    id: UUID
    query: str = Field(min_length=1, max_length=64000)
    vector: tuple[float, ...] = Field(min_length=1, max_length=2048)
    gold: str = Field(min_length=1, max_length=200)
    group_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    split: str = Field(pattern=r"^(validation|test)$")
    baseline_status: str = Field(default="unknown", pattern=r"^(completed|failed|unknown)$")
    baseline_output: str | None = Field(default=None, max_length=64000)

    @field_validator("vector", mode="before")
    @classmethod
    def valid_supplied_vector(cls, value):
        if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2048:
            raise ValueError("SEMANTIC_CACHE_QUERY_VECTOR_BOUND")
        unit_vector(value, len(value))
        return value

    @model_validator(mode="after")
    def known_baseline(self):
        if self.id.int == 0 or len(self.query.encode()) > 64000:
            raise ValueError("SEMANTIC_CACHE_QUERY_ID_OR_BYTE_BOUND")
        if (self.baseline_status == "completed") != (self.baseline_output is not None):
            raise ValueError("SEMANTIC_CACHE_EXPLICIT_BASELINE_STATUS_REQUIRED")
        return self


class CacheQualificationSpec(StrictModel):
    version: str = Field(
        default="cutback.semantic-cache-qualification.v1",
        pattern=r"^cutback\.semantic-cache-qualification\.v1$",
    )
    rule: SemanticCacheRule
    thresholds: tuple[float, ...] = Field(min_length=1, max_length=32)
    maximum_false_positive_group_upper95: float = Field(ge=0, le=1)
    minimum_useful_hit_group_ratio: float = Field(gt=0, le=1)
    maximum_utility_regression: float = Field(default=0, ge=0, le=1)
    minimum_negative_groups: int = Field(default=30, strict=True, ge=3, le=512)
    minimum_positive_groups: int = Field(default=10, strict=True, ge=3, le=512)

    @field_validator("thresholds", mode="before")
    @classmethod
    def finite_thresholds(cls, values):
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 32:
            raise ValueError("SEMANTIC_CACHE_CALIBRATION_GRID_BOUND")
        if any(
            type(value) not in {float, int} or not 0 <= value <= 1 or not math.isfinite(value)
            for value in values
        ):
            raise ValueError("SEMANTIC_CACHE_CALIBRATION_GRID_INVALID")
        if len(set(values)) != len(values):
            raise ValueError("SEMANTIC_CACHE_CALIBRATION_THRESHOLDS_MUST_BE_UNIQUE")
        return values

    @field_validator(
        "maximum_false_positive_group_upper95",
        "minimum_useful_hit_group_ratio",
        "maximum_utility_regression",
        mode="before",
    )
    @classmethod
    def finite_criterion(cls, value):
        if type(value) not in {float, int} or not 0 <= value <= 1 or not math.isfinite(value):
            raise ValueError("SEMANTIC_CACHE_FINITE_QUALIFICATION_CRITERION_REQUIRED")
        return value


def validate_queries(spec, cases):
    if not isinstance(cases, (list, tuple)) or not 2 <= len(cases) <= 512:
        raise ValueError("SEMANTIC_CACHE_QUALIFICATION_CASE_BOUND")
    identities, queries = set(), set()
    groups: dict[str, set[str]] = {"validation": set(), "test": set()}
    total = 0
    for case in cases:
        if not isinstance(case, CacheQualificationQuery):
            raise ValueError("SEMANTIC_CACHE_VALIDATED_QUERY_REQUIRED")
        unit_vector(case.vector, spec.rule.embedding.dimensions)
        fingerprint = digest({"query": case.query})
        if case.id in identities or fingerprint in queries:
            raise ValueError("SEMANTIC_CACHE_DUPLICATE_QUERY_CANNOT_INFLATE_COVERAGE")
        identities.add(case.id)
        queries.add(fingerprint)
        groups[case.split].add(case.group_sha256)
        total += (
            len(case.query.encode())
            + len(case.vector) * 8
            + len((case.baseline_output or "").encode())
        )
        if total > 32 * 1048576:
            raise ValueError("SEMANTIC_CACHE_QUALIFICATION_CONTENT_BOUND")
    if not all(groups.values()) or groups["validation"] & groups["test"]:
        raise ValueError("SEMANTIC_CACHE_DISJOINT_CALIBRATION_AND_CONFIRMATION_GROUPS_REQUIRED")


def _correct(gold, output):
    return (
        evaluate(
            [{"gold": gold}],
            [output],
            MetricSpec(kind="exact_label", minimum=0),
            independent_labels=False,
        )["value"]
        == 1
    )


def assess(index, cases, spec, *, neighbors=None, threshold=None):
    if not cases:
        raise ValueError("SEMANTIC_CACHE_ASSESSMENT_CASES_REQUIRED")
    answers = {anchor.identity: anchor.response for anchor in index.anchors}
    counts = {"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0}
    positive: set[str] = set()
    negative: set[str] = set()
    unsafe: set[str] = set()
    useful: set[str] = set()
    groups: set[str] = set()
    group_utility: dict[str, list[tuple[int | None, int | None]]] = {}
    baseline_correct, baseline_known, proposed_correct, proposed_known = 0, 0, 0, 0
    if neighbors is not None and len(neighbors) != len(cases):
        raise ValueError("SEMANTIC_CACHE_PAIRED_NEIGHBOR_COUNT_REQUIRED")
    policy = (
        index.rule if threshold is None else index.rule.model_copy(update={"threshold": threshold})
    )
    for ordinal, case in enumerate(cases):
        index.authority()
        observed = index.lookup(case.vector) if neighbors is None else neighbors[ordinal]
        match = select_match(
            policy,
            observed.anchor_id,
            observed.score,
            observed.distinct_answer_score,
            answers.get(observed.anchor_id),
        )
        valid_candidate = match.anchor_id is not None and _correct(
            case.gold, answers[match.anchor_id]
        )
        known_baseline = (
            None
            if case.baseline_status == "unknown"
            else int(
                case.baseline_status == "completed" and _correct(case.gold, case.baseline_output)
            )
        )
        group_utility.setdefault(case.group_sha256, []).append(
            (known_baseline, int(valid_candidate) if match.hit else known_baseline)
        )
        groups.add(case.group_sha256)
        (positive if valid_candidate else negative).add(case.group_sha256)
        counts[
            ("true_positive" if valid_candidate else "false_positive")
            if match.hit
            else ("false_negative" if valid_candidate else "true_negative")
        ] += 1
        if match.hit:
            (useful if valid_candidate else unsafe).add(case.group_sha256)
            proposed_known += 1
            proposed_correct += int(valid_candidate)
        if case.baseline_status != "unknown":
            baseline_known += 1
            correct = case.baseline_status == "completed" and _correct(
                case.gold, case.baseline_output
            )
            baseline_correct += int(correct)
            if not match.hit:
                proposed_known += 1
                proposed_correct += int(correct)
    n, hits = len(cases), counts["true_positive"] + counts["false_positive"]
    upper = wilson(len(unsafe), len(negative))[1]
    baseline = baseline_correct / n if baseline_known == n else None
    proposed = proposed_correct / n if proposed_known == n else None
    delta = proposed - baseline if proposed is not None and baseline is not None else None
    group_delta = None
    if baseline_known == proposed_known == n:
        group_delta = sum(
            sum(
                after - before
                for before, after in values
                if before is not None and after is not None
            )
            / len(values)
            for values in group_utility.values()
        ) / len(groups)
    useful_ratio = len(useful) / len(groups)
    reasons = []
    if len(negative) < spec.minimum_negative_groups:
        reasons.append("SEMANTIC_CACHE_INSUFFICIENT_NEGATIVE_CONTROL_GROUPS")
    if len(positive) < spec.minimum_positive_groups:
        reasons.append("SEMANTIC_CACHE_INSUFFICIENT_POSITIVE_CONTROL_GROUPS")
    if upper is None or upper > spec.maximum_false_positive_group_upper95:
        reasons.append("SEMANTIC_CACHE_FALSE_POSITIVE_GROUP_BOUND_FAILED")
    if useful_ratio < spec.minimum_useful_hit_group_ratio:
        reasons.append("SEMANTIC_CACHE_USEFUL_HIT_GROUP_GATE_FAILED")
    if group_delta is None or group_delta < -spec.maximum_utility_regression:
        reasons.append("SEMANTIC_CACHE_TASK_UTILITY_GATE_FAILED_OR_UNKNOWN")
    return {
        "cases": n,
        "metric_profile": "exact_label_v1",
        "declared_groups": len(groups),
        **counts,
        "hit_ratio": hits / n,
        "hit_precision": counts["true_positive"] / hits if hits else None,
        "false_hit_ratio": counts["false_positive"] / hits if hits else None,
        "positive_control_groups": len(positive),
        "negative_control_groups": len(negative),
        "unsafe_hit_groups": len(unsafe),
        "useful_hit_groups": len(useful),
        "useful_hit_group_ratio": useful_ratio,
        "false_positive_group_upper95": upper,
        "interval_method": "95pct_Wilson_any_bad_hit_per_declared_negative_control_group",
        "independence_assumption": "declared_groups_not_verified_population_independence",
        "baseline_known_cases": baseline_known,
        "proposed_known_cases": proposed_known,
        "baseline_quality": baseline,
        "proposed_quality": proposed,
        "utility_delta": delta,
        "group_weighted_utility_delta": group_delta,
        "passed": not reasons,
        "reasons": reasons,
        "embedding_cost_usd": None,
        "hosting_cost_usd": None,
        "complete_task_cost_usd": None,
        "realized_savings_usd": None,
    }


def calibrate(spec, anchors, cases, *, check=None):
    """Select using validation only. Durable server exposure is the caller's responsibility."""
    validate_queries(spec, cases)
    validation = [case for case in cases if case.split == "validation"]
    index = SemanticCacheIndex(spec.rule, anchors, check=check)
    neighbors = [index.lookup(case.vector) for case in validation]
    trials = []
    for threshold in sorted(spec.thresholds):
        result = assess(index, validation, spec, neighbors=neighbors, threshold=threshold)
        trials.append({"threshold": threshold, "assessment": result})
    qualified = [trial for trial in trials if trial["assessment"]["passed"]]
    selected = (
        max(
            qualified,
            key=lambda trial: (trial["assessment"]["useful_hit_group_ratio"], trial["threshold"]),
        )
        if qualified
        else None
    )
    return {
        "trials": trials,
        "selected_threshold": selected["threshold"] if selected else None,
        "selection_scope": "validation_only",
        "test_evaluated": False,
    }


def confirm(spec, anchors, cases, threshold, *, check=None):
    validate_queries(spec, cases)
    if type(threshold) not in {float, int} or threshold not in spec.thresholds:
        raise ValueError("SEMANTIC_CACHE_CONFIRMATION_REQUIRES_FROZEN_SELECTED_THRESHOLD")
    rule = spec.rule.model_copy(update={"threshold": threshold})
    return assess(
        SemanticCacheIndex(rule, anchors, check=check),
        [case for case in cases if case.split == "test"],
        spec,
    )
