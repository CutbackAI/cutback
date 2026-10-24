from decimal import Decimal
from uuid import uuid4

from cutback.evals.datasets import DatasetImport
from cutback.evals.experiments import Execution, ExperimentPlan


def dataset_body(n=80):
    return DatasetImport(
        workflow="classify",
        policy_id=str(uuid4()),
        source_type="synthetic_fixture",
        provenance_ref="synthetic-v1",
        independent_labels=True,
        cases=[
            {
                "id": f"case-{i}",
                "input": f"Classify synthetic {chr(65 + i // 26)}{chr(65 + i % 26)} fruit",
                "gold": "A",
            }
            for i in range(n)
        ],
    )


def plan_for(dataset, **changes):
    return ExperimentPlan.model_validate(
        {
            "workflow": "classify",
            "dataset_hash": dataset["immutable_hash"],
            "baseline": {
                "key": "baseline",
                "provider": "local",
                "model": "synthetic-baseline",
                "local_cost_upper_usd": "0.001",
            },
            "candidates": [
                {
                    "key": "candidate",
                    "provider": "local",
                    "model": "synthetic-candidate",
                    "local_cost_upper_usd": "0.001",
                }
            ],
            "metric": {
                "kind": "exact_label",
                "minimum": 0.7,
                "max_regression": 0.3,
                "min_test_samples": 3,
            },
            "budget": {"max_usd": "1", "max_requests": 500},
            "approved_providers": ["local"],
            **changes,
        }
    )


class SyntheticExecutor:
    def __init__(self, *, wrong=False, fail=False):
        self.calls = []
        self.wrong, self.fail = wrong, fail

    def execute(self, model, text, timeout_seconds):
        self.calls.append((model.key, text))
        if self.fail:
            raise TimeoutError("PRIVATE SECRET must not escape")
        return Execution(
            "B" if self.wrong and model.key == "candidate" else "A",
            Decimal("0.001" if model.key == "baseline" else "0.0002"),
            2,
        )
