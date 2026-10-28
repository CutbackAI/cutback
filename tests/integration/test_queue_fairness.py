"""Unstarted configuration failures cannot monopolize a shared worker."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import SyntheticExecutor, authorized_dataset, plan_for

from cutback.server.db import JobRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_missing_provider_does_not_starve_other_tenant_and_can_recheck(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    base, store, _, blocked_plan = authorized_dataset(api, tenant, tmp_path / "first", monkeypatch)
    second = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-queue-" + uuid4().hex}
    ).json()["id"]
    ready_base, _, ready_dataset, _ = authorized_dataset(
        api, (second, {}, {}), tmp_path / "second", monkeypatch, store=store
    )

    def queue(path, frozen):
        assert (
            client.post(
                path + f"/experiments/plans/{frozen['id']}/approve",
                headers=admin,
                json={"plan_hash": frozen["plan_hash"], "approval_ref": "synthetic-queue"},
            ).status_code
            == 200
        )
        response = client.post(path + "/experiments", headers=admin, json={"plan_id": frozen["id"]})
        assert response.status_code == 202
        return response.json()["id"]

    spec = plan_for(
        {"immutable_hash": ready_dataset["immutable_hash"]},
        baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1"},
        candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
        approved_providers=["openai"],
    )
    ready_plan = client.post(
        ready_base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": ready_dataset["id"], "specification": spec.model_dump(mode="json")},
    ).json()
    blocked_id, ready_id = queue(base, blocked_plan), queue(ready_base, ready_plan)
    with app.state.sessions.begin() as session:
        first = session.scalar(select(JobRecord).where(JobRecord.run_id == UUID(blocked_id)))
        first.created_at = datetime.now(UTC) - timedelta(minutes=1)

    class NativeFake(SyntheticExecutor):
        def close(self):
            pass

    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", NativeFake)

    def forbidden_native_client(*args, **kwargs):
        pytest.fail("Queue fairness must use its synthetic executor, never a provider client")

    monkeypatch.setattr("cutback.evals.executors.OpenAIExecutor", forbidden_native_client)
    monkeypatch.setattr("cutback.evals.executors.LocalExecutor", forbidden_native_client)
    monkeypatch.delenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT", raising=False)
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-no-paid-requests")
    assert run_one(app.state.sessions, store, execution_enabled=True)["status"] == "BLOCKED"
    assert run_one(app.state.sessions, store, execution_enabled=True)["run_id"] == ready_id
    blocked = client.get(base + f"/experiments/{blocked_id}", headers=admin).json()
    assert blocked["status"] == "BLOCKED" and blocked["requests"] == 0
    # A different tenant cannot reset eligibility or read the blocked run.
    viewer = client.post(ready_base + "/keys", headers=admin, json={"role": "editor"}).json()[
        "token"
    ]
    denied = client.post(
        base + f"/experiments/{blocked_id}/recheck",
        headers={"Authorization": "Bearer " + viewer},
        json={},
    )
    assert denied.status_code == 403
    monkeypatch.setenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT", "http://127.0.0.1:9999/v1")
    assert (
        client.post(base + f"/experiments/{blocked_id}/recheck", headers=admin, json={}).status_code
        == 200
    )
    assert run_one(app.state.sessions, store, execution_enabled=True)["status"] == "COMPLETED"
