"""Owned synthetic facts; never real customer or model performance evidence."""

import json
from decimal import Decimal
from uuid import uuid4

from cutback.core.compression import CompressionSettings, CompressionVariant
from cutback.evals.compression import CompressionExecutionSpec
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, Execution, ExperimentPlan, ModelSpec
from cutback.evals.metrics import MetricSpec


def fixture(rate="0.01"):
    baseline = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-4.1-mini",
        max_input_tokens=20000,
        max_output_tokens=128,
    )
    models = [
        baseline,
        *[baseline.model_copy(update={"key": key}) for key in ("compact", "unsafe")],
    ]
    settings = [
        CompressionSettings(max_context_bytes=1024),
        CompressionSettings(mode="deduplicate_lines", max_context_bytes=1024),
        CompressionSettings(mode="query_lines", max_context_bytes=64),
    ]
    context = (
        "refund deadline background " * 4
        + "\n"
        + ("deadline-days=14 " + "detail " * 10 + "\n")
        + "background notes\n" * 15
    )
    items = [
        {
            "case": {
                "id": f"case-{i}",
                "input": json.dumps({"query": f"refund deadline {word}", "context": context}),
                "gold": "14",
                "reference": "evaluator-only-compression-canary",
                "stratum": "default",
            },
            "split": "validation",
            "group_hash": digest(["compression-group", i]),
        }
        for i, word in enumerate(("amber", "birch", "cedar"))
    ]
    dataset = {"immutable_hash": digest(items), "items": items}
    plan = ExperimentPlan(
        workflow="compression-unit",
        dataset_hash=dataset["immutable_hash"],
        baseline=models[0],
        candidates=models[1:],
        metric=MetricSpec(kind="exact_label", minimum=0.9, min_test_samples=1, min_test_groups=1),
        budget=Budget(max_usd="1", max_requests=20),
        approved_providers=["openai"],
        split="validation",
        evaluator_version="deterministic-v7",
        content_nonce=uuid4(),
        retain_case_outputs=True,
        compression_execution=CompressionExecutionSpec(
            variants=tuple(
                CompressionVariant(model_key=m.key, settings=s)
                for m, s in zip(models, settings, strict=True)
            ),
            assumed_usd_per_cpu_hour=rate,
        ),
    )
    return plan, dataset


class NativeBoundary:
    def __init__(self, unknown=False):
        self.inputs = []
        self.unknown = unknown

    def execute(self, model, text, timeout_seconds):
        self.inputs.append(text)
        assert "evaluator-only-compression-canary" not in text
        frame = json.loads(text[text.index("{") :])
        return Execution(
            output="14" if "deadline-days=14" in frame["context"] else "wrong",
            cost_usd=None if self.unknown else Decimal(len(text.encode())) / Decimal(10000000),
            latency_ms=1,
            actual_model=model.model,
        )
