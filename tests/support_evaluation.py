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


def authorized_dataset(api, tenant, tmp_path, monkeypatch, *, store=None):
    import os
    from datetime import UTC, datetime, timedelta

    from cutback.evals.artifacts import EncryptedFileStore

    client, admin, app = api
    project = tenant[0]
    path = f"/v1/projects/{project}"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = store or EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    policy = client.post(
        path + "/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "synthetic-unit-test",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["local", "openai"],
            "sample_rate": 1,
            "max_samples": 100,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    ).json()["id"]
    data = dataset_body(40).model_copy(update={"policy_id": policy})
    dataset = client.post(
        path + "/datasets", headers=admin, json=data.model_dump(mode="json")
    ).json()
    spec = plan_for({"immutable_hash": dataset["immutable_hash"]})
    plan = client.post(
        path + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    ).json()
    return path, store, dataset, plan
