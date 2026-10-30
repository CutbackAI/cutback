"""Independent synthetic vector controls, deliberately not an embedding-quality benchmark."""

import json

from cutback.evals.datasets import Case, digest

EMBEDDING = {
    "adapter": "owned-synthetic-vector-controls",
    "revision_sha256": "a" * 64,
    "dimensions": 2,
}


def cases():
    result = []
    for index in range(500):
        symbol = "".join(chr(ord("a") + index // (26**power) % 26) for power in range(3))
        positive = index % 2 == 0
        label = "billing" if positive else "access"
        result.append(
            Case(
                id="control-" + symbol,
                group="declared-control-" + symbol,
                input=json.dumps(
                    {
                        "query": "Owned symbolic vector control " + symbol,
                        "vector": [1.0, 0.0] if positive else [0.8, 0.6],
                        "embedding_contract_sha256": digest(EMBEDDING),
                        "baseline_status": "completed",
                        "baseline_output": label,
                    }
                ),
                gold=label,
            )
        )
    return result


def qualification(rule):
    return {
        "rule": rule,
        "thresholds": [0.5, 0.99],
        "maximum_false_positive_group_upper95": 0.1,
        "minimum_useful_hit_group_ratio": 0.1,
    }
