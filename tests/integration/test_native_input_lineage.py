"""Real PostgreSQL and pinned SDKs, using owned synthetic HTTP transports only."""

import importlib
import json
from importlib.metadata import version
from uuid import UUID

import pytest
from sqlalchemy import func, select
from support_evaluation import authorized_dataset, dataset_body, plan_for
from test_dataset_lineage import approve_and_queue

from cutback.evals.datasets import CapturePolicy
from cutback.evals.executors import OpenAIExecutor
from cutback.server.dataset_lineage import LineageViolation, require_current_test_evidence
from cutback.server.db import (
    CapturePolicyRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    RecommendationRecord,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.plan_artifacts import freeze_plan
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def native_plan(dataset, *, lookup=False):
    parameter = {"op": "parameter", "name": "record"}
    contract = {
        "sdk_surface": "responses.create",
        "parameters": ["record"],
        "payload": {"op": "lookup", "value": parameter, "key": "question"} if lookup else parameter,
    }
    return plan_for(
        dataset,
        baseline={
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1",
            "request_contract": contract,
        },
        candidates=[
            {
                "key": "candidate",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "request_contract": contract,
            }
        ],
        approved_providers=["openai"],
    )


def executor(monkeypatch, calls):
    http = importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")

    def handle(request):
        value = json.loads(request.content)
        calls.append(value)
        return http.Response(
            200,
            json={
                "id": "resp_owned_synthetic",
                "object": "response",
                "created_at": 1,
                "model": value["model"],
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_owned",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "A", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-never-sent")
    native = OpenAIExecutor(http_client=http.Client(transport=http.MockTransport(handle)))
    native.synthetic_transport = True
    return native


def test_plan_and_legacy_worker_reject_rendered_split_leakage_before_billing(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    base, store, original, old_plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    body = dataset_body(40).model_copy(update={"policy_id": original["provenance"]["policy_id"]})
    for case in body.cases:
        case.input = json.dumps({"question": "owned fixed task", "unused": case.input})
    imported = client.post(base + "/datasets", headers=admin, json=body.model_dump(mode="json"))
    assert imported.status_code == 201, imported.text
    dataset = imported.json()
    spec = native_plan(dataset, lookup=True)
    request = {"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")}
    denied = client.post(base + "/experiments/plans", headers=admin, json=request)
    assert denied.status_code == 422
    assert denied.json()["code"] == "NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS"
    assert (
        client.post(base + "/experiments/plans", headers=tenant[2], json=request).status_code == 403
    )
    # An already frozen pre-guard plan must also fail: do not rely only on the API.
    with app.state.sessions.begin() as session:
        row = session.get(ExperimentPlanRecord, UUID(old_plan["id"]))
        policy_row = session.get(CapturePolicyRecord, UUID(body.policy_id))
        row.dataset_id = UUID(dataset["id"])
        row.specification = freeze_plan(
            store,
            row.project_id,
            spec,
            CapturePolicy.model_validate(policy_row.policy),
            policy_row.id,
        )
        old_plan["plan_hash"] = row.specification["plan_hash"]
    run_id = approve_and_queue(client, admin, base, old_plan)
    calls = []
    native = executor(monkeypatch, calls)
    try:
        result = run_one(
            app.state.sessions, store, executor=native, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] == "FAILED"
        assert result["results"]["code"] == "NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS"
        assert calls == []
        with app.state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(run_id))
            assert run.requests_used == 0 and run.reserved_usd == 0
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ExecutionCall)
                    .where(ExecutionCall.run_id == UUID(run_id))
                )
                == 0
            )
    finally:
        native.close()


def test_actual_native_inputs_and_receipt_are_rechecked_before_source_review(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    spec = native_plan(dataset)
    created = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    )
    assert created.status_code == 201, created.text
    assert created.json()["input_grouping"]["selected_effective_groups"] >= 3
    run_id = approve_and_queue(client, admin, base, created.json())
    calls = []
    native = executor(monkeypatch, calls)
    try:
        result = run_one(
            app.state.sessions, store, executor=native, execution_enabled=True, only_run_id=run_id
        )
    finally:
        native.close()
    assert result["status"] == "COMPLETED", result
    assert len(calls) == result["results"]["requests"] > 0
    with app.state.sessions.begin() as session:
        recommendation = session.scalar(
            select(RecommendationRecord).where(RecommendationRecord.run_id == UUID(run_id))
        )
        assert recommendation.evidence["quality_gate_passed"]
        assert require_current_test_evidence(session, store, recommendation)["fresh_test_claim"]
        recommendation.evidence = {
            key: value for key, value in recommendation.evidence.items() if key != "input_grouping"
        }
        with pytest.raises(LineageViolation, match="NATIVE_INPUT_GROUPING_RECONFIRMATION_REQUIRED"):
            require_current_test_evidence(session, store, recommendation)
    assert "Classify synthetic" not in json.dumps(result)


def test_input_preparation_expiry_never_dispatches_or_holds_a_native_call(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.core.request_profiles import RequestContract
    from cutback.evals import experiments

    client, admin, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    spec = native_plan(dataset)
    created = client.post(
        base + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    )
    assert created.status_code == 201, created.text
    run_id = approve_and_queue(client, admin, base, created.json())
    calls = []
    native = executor(monkeypatch, calls)
    original = RequestContract.render
    started = experiments.time.monotonic()
    clock = [started]

    def render(self, text):
        clock[0] = started + spec.budget.max_wall_seconds + 1
        return original(self, text)

    monkeypatch.setattr(experiments.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(RequestContract, "render", render)
    try:
        result = run_one(
            app.state.sessions, store, executor=native, execution_enabled=True, only_run_id=run_id
        )
    finally:
        native.close()
    assert result["status"] == "FAILED"
    assert result["results"]["code"] == "NATIVE_INPUT_GROUPING_WALL_LIMIT"
    assert calls == []
    with app.state.sessions() as session:
        run = session.get(ExperimentRunRecord, UUID(run_id))
        assert run.requests_used == 0 and run.reserved_usd == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExecutionCall)
                .where(ExecutionCall.run_id == UUID(run_id))
            )
            == 0
        )
