"""Joint search uses real PostgreSQL intents and installed native SDK transports."""

import json
from uuid import uuid4

import httpx
import httpx2
import pytest
from sqlalchemy import select
from support_compression_application import application
from support_native_agent import native_executor, response_body
from support_retrieval_application import git
from test_compression_workflow import prepare, queue

from cutback.joint_context_changes import JointContextProposal, inspect_producer, prepare_patch
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_unbound_anthropic_joint_context_runs_without_inventing_source_coverage(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, legacy = prepare(
        api, tenant, tmp_path, monkeypatch, surface="messages.create"
    )
    body = {key: value for key, value in legacy.items() if key != "candidates"}
    body["models"] = []
    body["contexts"] = legacy["candidates"]
    planned = api[0].post(base + "/model-context/draft", headers=api[1], json=body)
    assert planned.status_code == 201, planned.text
    calls = []

    def handle(request):
        raw = json.loads(request.content)
        text = raw["messages"][0]["content"]
        assert "evaluator-only-compression-canary" not in text
        frame = json.loads(text[text.index("{") :])
        calls.append(raw)
        response = response_body("messages.create")
        response["content"][0]["text"] = "14" if "deadline-days=14" in frame["context"] else "wrong"
        response["usage"].update(input_tokens=len(text.encode()), output_tokens=2)
        return httpx2.Response(200, json=response)

    executor = native_executor("messages.create", monkeypatch, handle)
    try:
        run_id = queue(api, base, planned.json())
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "COMPLETED"
        )
        confirmed = api[0].post(
            base + "/model-context/confirmation",
            headers=api[1],
            json={"validation_run_id": str(run_id), "budget": body["budget"]},
        )
        assert confirmed.status_code == 201, confirmed.text
        confirmed_id = queue(api, base, confirmed.json())
        assert (
            run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=confirmed_id,
            )["status"]
            == "COMPLETED"
        )
        proposed = api[0].post(
            base + f"/model-context/{confirmed_id}/proposal",
            headers=api[1],
            json={"purpose": "owned-unbound-review"},
        )
        assert (
            proposed.status_code == 409
            and proposed.json()["code"] == "JOINT_QUALIFIED_SOURCE_PRODUCER_REQUIRED"
        )
        assert calls
    finally:
        executor.close()


def draft(api, tenant, tmp_path, monkeypatch):
    root = application(tmp_path)
    path = root / "app.py"
    path.write_bytes(path.read_bytes().replace(b'model="gpt-4.1-mini"', b'model="gpt-4.1"'))
    git(root, "add", ".")
    git(root, "commit", "-m", "Owned joint-search baseline")
    _, binding, producer = inspect_producer(root, "app.py")
    base, store, _, policy, legacy = prepare(api, tenant, tmp_path, monkeypatch, binding=binding)
    body = {key: value for key, value in legacy.items() if key != "candidates"}
    body["baseline"]["model"] = "gpt-4.1"
    body["models"] = [{**body["baseline"], "key": "smaller", "model": "gpt-4.1-mini"}]
    body["contexts"] = legacy["candidates"]
    body["producer_binding"] = producer.model_dump(mode="json")
    planned = api[0].post(base + "/model-context/draft", headers=api[1], json=body)
    assert planned.status_code == 201, planned.text
    return base, store, planned.json(), policy, body, root


@pytest.mark.parametrize("mode", ["success", "outage", "missing_usage"])
def test_joint_native_ledger_confirmation_and_scope(api, tenant, tmp_path, monkeypatch, mode):
    base, store, plan, policy, body, root = draft(api, tenant, tmp_path, monkeypatch)
    assert (
        api[0].post(base + "/model-context/draft", headers=tenant[2], json=body).status_code == 403
    )
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "joint-" + uuid4().hex[:12]})
        .json()["id"]
    )
    assert (
        api[0]
        .post(f"/v1/projects/{other}/model-context/draft", headers=api[1], json=body)
        .status_code
        == 404
    )
    run_id = queue(api, base, plan)
    validation_id = run_id
    calls = []

    def handle(request):
        with api[2].state.sessions() as session:
            intent = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
            )
            assert intent is not None and intent.state == "reserved"
        raw = json.loads(request.content)
        text = raw["input"]
        assert "evaluator-only-compression-canary" not in text
        frame = json.loads(text[text.index("{") :])
        calls.append(raw)
        if mode == "outage":
            raise httpx.ConnectError("OWNED_PRIVATE_JOINT_OUTAGE")
        response = response_body("responses.create")
        correct = "deadline-days=14" in frame["context"] and (
            raw["model"] == "gpt-4.1" or len(frame["context"].splitlines()) <= 3
        )
        response["output"][0]["content"][0]["text"] = "14" if correct else "wrong"
        response["model"] = raw["model"]
        response["usage"].update(
            input_tokens=len(text.encode()), output_tokens=2, total_tokens=len(text.encode()) + 2
        )
        if mode == "missing_usage":
            response.pop("usage")
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            assert run.requests_used == len(calls)
            assert "deadline-days" not in json.dumps(run.results)
            assert "OWNED_PRIVATE_JOINT_OUTAGE" not in json.dumps(run.results)
            if mode != "success":
                assert result["status"] == "FAILED" and len(calls) == 1
                assert not run.results["billing_complete"] and run.reserved_usd > 0
                return
            assert result["status"] == "COMPLETED", result
            assert any(
                row["joint_model_context_confirmation_eligible"]
                for row in run.results["recommendations"]
            )
        confirmation = api[0].post(
            base + "/model-context/confirmation",
            headers=api[1],
            json={"validation_run_id": str(run_id), "budget": body["budget"]},
        )
        assert confirmation.status_code == 201, confirmation.text
        legacy = api[0].post(
            base + "/compression/confirmation",
            headers=api[1],
            json={"validation_run_id": str(run_id), "budget": body["budget"]},
        )
        assert legacy.status_code == 409
        confirmed_id = queue(api, base, confirmation.json())
        # The handler verifies the current run's durable intent, including confirmation.
        run_id = confirmed_id
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=confirmed_id,
        )
        assert result["status"] == "COMPLETED", result
        with api[2].state.sessions() as session:
            confirmed = session.get(ExperimentRunRecord, confirmed_id)
            assert confirmed.results["lineage"]["fresh_test_claim"] is True
        assert (
            api[0]
            .post(
                base + f"/model-context/{validation_id}/proposal",
                headers=api[1],
                json={"purpose": "owned-source-review"},
            )
            .status_code
            == 409
        )
        assert (
            api[0]
            .post(
                base + f"/model-context/{confirmed_id}/proposal",
                headers=tenant[2],
                json={"purpose": "owned-source-review"},
            )
            .status_code
            == 403
        )
        proposed = api[0].post(
            base + f"/model-context/{confirmed_id}/proposal",
            headers=api[1],
            json={"purpose": "owned-source-review"},
        )
        assert proposed.status_code == 200, proposed.text
        proposal = JointContextProposal.model_validate(proposed.json())
        assert proposal.decision == "synthetic_local_review_only"
        artifact = prepare_patch(root, proposal)
        assert set(artifact["proposed_files"]) == {"app.py", "compression.json"}
        repeated = api[0].post(
            base + "/model-context/confirmation",
            headers=api[1],
            json={
                "validation_run_id": str(validation_id),
                "budget": body["budget"],
            },
        )
        assert repeated.status_code == 409
        assert (
            api[0].delete(base + f"/capture-policies/{policy}", headers=api[1]).status_code == 200
        )
        revoked = api[0].post(
            base + f"/model-context/{confirmed_id}/proposal",
            headers=api[1],
            json={"purpose": "owned-source-review"},
        )
        assert revoked.status_code in {403, 409}
    finally:
        executor.close()
