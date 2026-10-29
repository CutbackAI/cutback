"""Owned synthetic retrieval fixtures; no paid model transport."""

import json
from decimal import Decimal
from uuid import UUID, uuid4

from cutback.core.retrieval import (
    CorpusDocument,
    RetrievalCorpus,
    RetrievalSettings,
    RetrievalVariant,
)
from cutback.evals.datasets import digest
from cutback.evals.experiments import (
    Budget,
    Execution,
    ExperimentPlan,
    ModelSpec,
)
from cutback.evals.metrics import MetricSpec
from cutback.evals.retrieval import RetrievalExecutionSpec


def fixture(rate="0.01"):
    text = "refund return deadline " + "policy " * 24 + "fourteen-days " + "background " * 100
    corpus = RetrievalCorpus(documents=(CorpusDocument(document_id=UUID(int=1), text=text),))
    base = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-4.1-mini",
        max_input_tokens=32768,
        max_output_tokens=128,
    )
    models = [
        base,
        base.model_copy(update={"key": "compact"}),
        base.model_copy(update={"key": "unsafe"}),
    ]
    policies = [RetrievalSettings(chunk_chars=n, overlap_chars=0, top_k=1) for n in (512, 256, 128)]
    retrieval = RetrievalExecutionSpec(
        corpus=corpus,
        variants=tuple(
            RetrievalVariant(model_key=m.key, settings=s)
            for m, s in zip(models, policies, strict=True)
        ),
        assumed_usd_per_cpu_hour=rate,
    )
    items = [
        {
            "case": {
                "id": f"case-{i}",
                "input": f"refund return deadline {i}",
                "gold": "14",
                "reference": "evaluator-only-reference-canary",
                "stratum": "default",
            },
            "split": "validation",
            "group_hash": digest(["group", i]),
        }
        for i in range(3)
    ]
    dataset = {"immutable_hash": digest(items), "items": items}
    plan = ExperimentPlan(
        workflow="retrieval-unit",
        dataset_hash=dataset["immutable_hash"],
        baseline=models[0],
        candidates=models[1:],
        metric=MetricSpec(kind="exact_label", minimum=0.9, min_test_samples=1, min_test_groups=1),
        budget=Budget(max_usd="1", max_requests=20),
        approved_providers=["openai"],
        split="validation",
        evaluator_version="deterministic-v6",
        content_nonce=uuid4(),
        retrieval_execution=retrieval,
        retain_case_outputs=True,
    )
    return plan, dataset


class NativeBoundary:
    def __init__(self, *, unknown=False):
        self.inputs = []
        self.unknown = unknown

    def execute(self, model, text, timeout_seconds):
        self.inputs.append(text)
        frame = json.loads(text[text.index("{") :])
        assert "evaluator-only-reference-canary" not in text
        answer = "14" if any("fourteen-days" in d["text"] for d in frame["documents"]) else "wrong"
        return Execution(
            output=answer,
            cost_usd=None if self.unknown else Decimal(len(text.encode())) / Decimal(10000000),
            latency_ms=1,
            actual_model=model.model,
        )
