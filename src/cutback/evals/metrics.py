"""Deterministic metrics. Semantic verification requires independent held-out labels."""

import hashlib
import json
import math
from collections import Counter
from typing import Literal

from pydantic import Field, model_validator

from cutback.core.models import StrictModel


class MetricSpec(StrictModel):
    output_field: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    kind: Literal[
        "exact_label",
        "field_f1",
        "json_schema",
        "nonempty",
        "reference_match",
        "allowed_label",
        "span_support",
        "retrieval_recall",
        "ndcg",
        "rank_agreement",
        "numeric_match",
    ]
    minimum: float = Field(ge=0, le=1)
    max_regression: float = Field(default=0, ge=0, le=1)
    min_test_samples: int = Field(default=30, ge=1, le=10000)
    min_test_groups: int = Field(default=3, ge=1, le=10000)
    schema_definition: dict | None = None
    allowed_labels: list[str] = Field(default_factory=list, max_length=1000)
    cutoff: int = Field(default=10, ge=1, le=1000)
    numeric_tolerance: float = Field(default=0, ge=0, le=1000)

    @model_validator(mode="after")
    def safe_schema(self):
        if self.kind == "allowed_label" and (
            not self.allowed_labels or any(len(label) > 200 for label in self.allowed_labels)
        ):
            raise ValueError("bounded allowed labels required")

        def inspect(value, depth=0):
            if depth > 30:
                raise ValueError("schema nesting exceeds limit")
            if isinstance(value, dict):
                if any(k in value for k in ("pattern", "patternProperties")):
                    raise ValueError(
                        "untrusted regex evaluation requires an isolated custom evaluator"
                    )
                if any(k in value for k in ("$ref", "$dynamicRef")):
                    raise ValueError("evaluation schemas cannot resolve external or recursive refs")
                for nested in value.values():
                    inspect(nested, depth + 1)
            elif isinstance(value, list):
                for nested in value:
                    inspect(nested, depth + 1)

        if self.kind == "json_schema":
            if not self.schema_definition:
                raise ValueError("JSON schema required")
            inspect(self.schema_definition)
            from jsonschema import Draft202012Validator

            Draft202012Validator.check_schema(self.schema_definition)
        return self


def wilson(success: int, n: int) -> tuple[float | None, float | None]:
    if not n:
        return None, None
    z = 1.959963984540054
    p = success / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0, center - half), min(1, center + half)


def evaluate(
    cases: list[dict], outputs: list[str | None], metric: MetricSpec, *, independent_labels: bool
) -> dict:
    if len(cases) != len(outputs) or not cases:
        raise ValueError("paired nonempty cases/outputs required")
    if metric.output_field:
        field = metric.output_field
        projected_cases, projected_outputs = [], []
        for case, output in zip(cases, outputs, strict=True):
            gold = case.get("gold")
            projected_cases.append(
                {**case, "gold": gold.get(field) if isinstance(gold, dict) else None}
            )
            try:
                parsed = json.loads(output) if output is not None else None
                value = parsed[field] if isinstance(parsed, dict) else None
                projected_outputs.append(str(value) if value is not None else None)
            except (ValueError, KeyError, TypeError):
                projected_outputs.append(None)
        return evaluate(
            projected_cases,
            projected_outputs,
            metric.model_copy(update={"output_field": None}),
            independent_labels=independent_labels,
        )
    exact, valid, tp, fp, fn = 0, 0, 0, 0, 0
    labels_available = all(case.get("gold") is not None for case in cases)
    confusion: Counter = Counter()
    values = []
    for case, output in zip(cases, outputs, strict=True):
        score = 0.0
        counted_fields = False
        if output is not None:
            try:
                if metric.kind == "exact_label":
                    label = output.strip()
                    score = float(labels_available and label == str(case["gold"]))
                    confusion[(str(case.get("gold")), label)] += 1
                elif metric.kind == "field_f1":
                    parsed = json.loads(output)
                    gold = case.get("gold")
                    if not isinstance(parsed, (dict, list)) or not isinstance(gold, (dict, list)):
                        raise ValueError("structured labels required")
                    predicted = (
                        {json.dumps([k, v], sort_keys=True) for k, v in parsed.items()}
                        if isinstance(parsed, dict)
                        else set(map(str, parsed))
                    )
                    expected = (
                        {json.dumps([k, v], sort_keys=True) for k, v in gold.items()}
                        if isinstance(gold, dict)
                        else set(map(str, gold))
                    )
                    a, b, c = (
                        len(predicted & expected),
                        len(predicted - expected),
                        len(expected - predicted),
                    )
                    tp, fp, fn = tp + a, fp + b, fn + c
                    counted_fields = True
                    score = 2 * a / (2 * a + b + c) if (2 * a + b + c) else 1.0
                elif metric.kind == "json_schema":
                    from jsonschema import Draft202012Validator

                    score = float(
                        Draft202012Validator(metric.schema_definition).is_valid(json.loads(output))
                    )
                elif metric.kind == "reference_match":
                    score = float(
                        case.get("reference") is not None
                        and output.strip() == case["reference"].strip()
                    )
                elif metric.kind in {
                    "allowed_label",
                    "span_support",
                    "retrieval_recall",
                    "ndcg",
                    "rank_agreement",
                    "numeric_match",
                }:
                    from cutback.evals.metrics_extra import score_extra

                    score = score_extra(case, output, metric)
                else:
                    score = float(bool(output.strip()))
                if metric.kind not in {"nonempty", "json_schema"} or score == 1:
                    valid += 1
            except (ValueError, TypeError, KeyError):
                pass
        if metric.kind == "field_f1" and not counted_fields:
            gold = case.get("gold")
            fn += len(gold) if isinstance(gold, (dict, list)) else 1
        exact += int(score == 1)
        values.append(score)
    mean = sum(values) / len(values)
    if metric.kind == "field_f1":
        mean = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else mean
    binary = metric.kind in {
        "exact_label",
        "json_schema",
        "nonempty",
        "reference_match",
        "allowed_label",
    }
    lower, upper = wilson(exact, len(cases)) if binary else (None, None)
    tier = (
        4
        if independent_labels
        and labels_available
        and metric.kind
        in {
            "exact_label",
            "field_f1",
            "retrieval_recall",
            "ndcg",
            "rank_agreement",
            "numeric_match",
        }
        else 2
        if metric.kind == "reference_match"
        else 1
    )
    return {
        "metric": metric.kind,
        "value": mean,
        "sample_n": len(cases),
        "failures": len(cases) - valid,
        "perfect_case_count": exact,
        "confidence_95": {
            "low": lower,
            "high": upper,
            "method": "wilson" if binary else "not_computed_for_micro_f1",
        },
        "evidence_tier": tier,
        "semantic_quality": "heldout_labeled_metric" if tier == 4 else "NOT VERIFIED",
        "confusion_matrix": [
            {
                "gold_hash": hashlib.sha256(gold.encode()).hexdigest(),
                "predicted_hash": hashlib.sha256(predicted.encode()).hexdigest(),
                "count": count,
            }
            for (gold, predicted), count in sorted(confusion.items())
        ],
        "field_counts": {"tp": tp, "fp": fp, "fn": fn},
        "scale": "(spearman_rho+1)/2" if metric.kind == "rank_agreement" else "unit_interval",
    }


def grouped_confidence(cases, outputs, groups, metric, evidence):
    """Cluster bootstrap resamples whole groups, preserving duplicate/customer dependence."""
    import random
    from collections import defaultdict

    grouped = defaultdict(list)
    for index, group in enumerate(groups):
        grouped[group].append(index)
    evidence["independent_group_n"] = len(grouped)
    if len(grouped) < 2 or metric.kind not in {
        "exact_label",
        "field_f1",
        "retrieval_recall",
        "ndcg",
        "rank_agreement",
        "numeric_match",
    }:
        evidence["confidence_95"] = {
            "low": None,
            "high": None,
            "method": "insufficient_independent_groups",
        }
        return evidence
    rng = random.Random(17)
    keys = sorted(grouped)
    values = []
    # Bounded reproducible bootstrap; class/field metrics are descriptive for this heldout set.
    for _ in range(200):
        indexes = [index for group in rng.choices(keys, k=len(keys)) for index in grouped[group]]
        score = evaluate(
            [cases[i] for i in indexes],
            [outputs[i] for i in indexes],
            metric,
            independent_labels=False,
        )["value"]
        values.append(score)
    values.sort()
    if metric.kind != "exact_label" and values[0] == values[-1]:
        evidence["confidence_95"] = {
            "low": 0.0,
            "high": 1.0,
            "method": "degenerate_bootstrap_unknown_population_interval",
        }
        return evidence
    # Perfect observations still have sampling uncertainty; bootstrap alone degenerates at 1.0.
    group_success = sum(
        all(
            evaluate([cases[i]], [outputs[i]], metric, independent_labels=False)["value"] == 1
            for i in indexes
        )
        for indexes in grouped.values()
    )
    low, high = wilson(group_success, len(keys))
    evidence["confidence_95"] = {
        "low": min(values[4], low) if metric.kind == "exact_label" else values[4],
        "high": max(values[-5], high) if metric.kind == "exact_label" else values[-5],
        "method": "seed17_cluster_bootstrap_200_plus_exact_label_wilson"
        if metric.kind == "exact_label"
        else "seed17_cluster_bootstrap_200",
    }
    return evidence
