"""Bounded, deterministic retrieval/ranking/source and numerical checks. No network I/O."""

import json
import math
from decimal import Decimal, InvalidOperation


def sequence(value):
    if not isinstance(value, list) or not value or len(value) > 1000:
        raise ValueError("nonempty ranked list required")
    if not all(isinstance(item, str) for item in value) or len(set(value)) != len(value):
        raise ValueError("unique string IDs required")
    return value


def score_extra(case, output, metric):
    if len(output) > 256000:
        raise ValueError("output exceeds evaluator bound")
    if metric.kind == "allowed_label":
        return float(output.strip() in metric.allowed_labels)
    parsed = json.loads(output)
    if metric.kind == "span_support":
        quotes = sequence(parsed)
        return sum(quote in case["input"] for quote in quotes) / len(quotes)
    if metric.kind == "numeric_match":
        gold = case.get("gold")
        if not isinstance(gold, dict) or not gold or len(gold) > 1000:
            raise ValueError("numeric field gold required")
        if not isinstance(parsed, dict):
            raise ValueError("numeric object required")
        matches = 0
        for key, truth in gold.items():
            try:
                actual, expected = Decimal(str(parsed[key])), Decimal(str(truth))
                if not actual.is_finite() or not expected.is_finite():
                    raise ValueError("nonfinite number")
                matches += abs(actual - expected) <= Decimal(str(metric.numeric_tolerance))
            except (KeyError, ValueError, InvalidOperation):
                continue
        # Extra invented fields are penalized along with missing/incorrect fields.
        return matches / max(len(gold), len(parsed))
    ranked = sequence(parsed)
    gold = case.get("gold")
    if metric.kind == "rank_agreement":
        truth = sequence(gold)
        if len(ranked) < 2 or set(ranked) != set(truth):
            raise ValueError("complete paired ranking required")
        positions = {item: i for i, item in enumerate(truth)}
        d2 = sum((i - positions[item]) ** 2 for i, item in enumerate(ranked))
        rho = 1 - 6 * d2 / (len(ranked) * (len(ranked) ** 2 - 1))
        return (rho + 1) / 2  # Explicit unit interval scale, not raw Spearman rho.
    if isinstance(gold, list):
        relevant = {item: 1.0 for item in sequence(gold)}
    elif isinstance(gold, dict) and 0 < len(gold) <= 1000:
        relevant = {str(key): float(value) for key, value in gold.items()}
        if any(not math.isfinite(value) or not 0 <= value <= 10 for value in relevant.values()):
            raise ValueError("invalid relevance grade")
    else:
        raise ValueError("relevance gold required")
    positive = {key for key, value in relevant.items() if value > 0}
    if not positive:
        raise ValueError("no relevant gold items")
    selected = ranked[: metric.cutoff]
    if metric.kind == "retrieval_recall":
        return len(positive & set(selected)) / len(positive)
    ideal = sorted(relevant.values(), reverse=True)[: metric.cutoff]
    dcg = sum((2 ** relevant.get(key, 0) - 1) / math.log2(i + 2) for i, key in enumerate(selected))
    idcg = sum((2**value - 1) / math.log2(i + 2) for i, value in enumerate(ideal))
    return dcg / idcg
