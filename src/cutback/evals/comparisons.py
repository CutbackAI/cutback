"""Descriptive paired case-score deltas; cluster resampling preserves case dependence."""

import random
from collections import defaultdict

from cutback.evals.metrics import evaluate


def paired_delta(cases, baseline, candidate, groups, metric):
    buckets = defaultdict(list)
    deltas = []
    for case, before, after, group in zip(cases, baseline, candidate, groups, strict=True):
        delta = (
            evaluate([case], [after], metric, independent_labels=False)["value"]
            - evaluate([case], [before], metric, independent_labels=False)["value"]
        )
        deltas.append(delta)
        buckets[group].append(delta)
    mean = sum(deltas) / len(deltas)
    bounds = {"low": None, "high": None}
    if len(buckets) >= 2:
        rng = random.Random(17)
        values = list(buckets.values())
        samples = []
        for _ in range(200):
            sample = [v for bucket in rng.choices(values, k=len(values)) for v in bucket]
            samples.append(sum(sample) / len(sample))
        samples.sort()
        bounds = {"low": samples[4], "high": samples[-5]}
    return {
        "sample_n": len(deltas),
        "improved": sum(v > 0 for v in deltas),
        "regressed": sum(v < 0 for v in deltas),
        "tied": sum(v == 0 for v in deltas),
        "mean_case_score_difference": mean,
        "confidence_95": bounds,
        "scope": "descriptive_first_repeat_case_scores_not_population_verification",
        "method": "seed17_paired_cluster_bootstrap_200",
    }
