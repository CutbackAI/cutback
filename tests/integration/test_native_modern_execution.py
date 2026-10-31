"""One-shot native usage uncertainty through real PostgreSQL and exact approvals."""

import importlib
from importlib.metadata import version
from uuid import UUID

import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset, plan_for

from cutback.core.native_models import NativeModelContract
from cutback.evals.executors import OpenAIExecutor
from cutback.evals.experiments import ModelSpec, upper_cost
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("damage", ["missing_tier", "fast", "missing_usage"])
def test_unknown_native_cost_keeps_reservation_and_cannot_retry_or_approve(
    api, tenant, tmp_path, monkeypatch, damage
):
    client, admin, app = api
    path, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    baseline = ModelSpec(
        key="baseline",
        provider="openai",
        model="gpt-6-luna",
        native_contract=NativeModelContract.current_luna_text(),
    )
    specification = plan_for(
        {"immutable_hash": dataset["immutable_hash"]},
        baseline=baseline.model_dump(mode="json"),
        candidates=[baseline.model_copy(update={"key": "candidate", "max_output_tokens": 128})],
        approved_providers=["openai"],
    )
    created = client.post(
        path + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": specification.model_dump(mode="json")},
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    approved = client.post(
        path + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-modern-native-only"},
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(path + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 202, queued.text
    run_id = UUID(queued.json()["id"])
    calls = []
    http = importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")

    def handle(request):
        calls.append(request)
        wire = {
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 1,
            "model": "gpt-6-luna",
            "status": "completed",
            "service_tier": "default",
            "output": [
                {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "A", "annotations": []}],
                }
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 2,
                "total_tokens": 22,
                "input_tokens_details": {
                    "cached_tokens": 0,
                    "cache_write_tokens": 0,
                    "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0},
                },
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        if damage == "missing_tier":
            wire.pop("service_tier")
        elif damage == "fast":
            wire["service_tier"] = "priority"
        else:
            wire.pop("usage")
        return http.Response(200, json=wire)

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-sent")
    executor = OpenAIExecutor(http_client=http.Client(transport=http.MockTransport(handle)))
    executor.synthetic_transport = True
    try:
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] != "COMPLETED" and len(calls) == 1
        with app.state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            receipts = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert run.requests_used == 1 and run.known_cost_requests == 0
            assert run.spent_usd == 0 and run.reserved_usd == upper_cost(baseline)
            assert len(receipts) == 1 and receipts[0].spent_usd is None
            assert receipts[0].response_model == "gpt-6-luna"
            assert receipts[0].price_sha256 == baseline.native_contract.price_sha256
            assert run.results.get("recommendations", []) == []
        run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert len(calls) == 1
        assert (
            client.post(
                path + "/experiments", headers=admin, json={"plan_id": plan["id"]}
            ).status_code
            == 403
        )
    finally:
        executor.close()
