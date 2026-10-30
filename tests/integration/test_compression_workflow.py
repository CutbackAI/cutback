"""Actual SDK transports, encrypted content and durable PostgreSQL reservations."""

import json
from uuid import UUID

import httpx
import httpx2
import pytest
from sqlalchemy import select
from support_compression import fixture
from support_evaluation import authorized_dataset, dataset_body
from support_native_agent import native_executor, response_body

from cutback.server.db import (
    CapturePolicyRecord,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
    Project,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepare(api, tenant, tmp_path, monkeypatch, *, surface="responses.create", binding=None):
    client, admin, app = api
    base, store, initial, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions() as session:
        policy_id = session.get(DatasetRecord, UUID(initial["id"])).provenance["policy_id"]
        policy = session.get(CapturePolicyRecord, UUID(policy_id)).policy
    if surface == "messages.create":
        result = client.post(
            base + "/capture-policies",
            headers=admin,
            json={**policy, "allowed_providers": ["anthropic"]},
        )
        assert result.status_code == 201
        policy_id = result.json()["id"]
    plan, sample = fixture()
    context = json.loads(sample["items"][0]["case"]["input"])["context"]
    data = dataset_body(24).model_dump(mode="json")
    data["policy_id"] = policy_id
    for i, case in enumerate(data["cases"]):
        case.update(
            input=json.dumps({"query": "refund deadline plan " + chr(65 + i), "context": context}),
            gold="14",
            reference="evaluator-only-compression-canary",
        )
    dataset = client.post(base + "/datasets", headers=admin, json=data)
    assert dataset.status_code == 201, dataset.text
    baseline = plan.baseline.model_dump(mode="json")
    if surface == "messages.create":
        baseline.update(provider="anthropic", model="claude-haiku-4-5")
    body = {
        "dataset_id": dataset.json()["id"],
        "baseline": baseline,
        "settings": plan.compression_execution.variants[0].settings.model_dump(mode="json"),
        "candidates": [
            v.settings.model_dump(mode="json") for v in plan.compression_execution.variants[1:]
        ],
        "metric": plan.metric.model_dump(mode="json"),
        "budget": {"max_usd": "1", "max_requests": 100},
        "assumed_usd_per_cpu_hour": "0.01",
        "retain_case_outputs": True,
        "configuration_binding": binding.model_dump(mode="json") if binding else None,
    }
    response = client.post(base + "/compression/draft", headers=admin, json=body)
    assert response.status_code == 201, response.text
    pending = response.json()
    with app.state.sessions() as session:
        row = session.get(ExperimentPlanRecord, UUID(pending["id"]))
        assert "deadline-days" not in json.dumps(row.specification)
    return base, store, pending, UUID(policy_id), body


def queue(api, base, plan):
    client, admin, _ = api
    approved = client.post(
        base + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-compression-synthetic-only"},
    )
    assert approved.status_code == 200, approved.text
    response = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return UUID(response.json()["id"])


@pytest.mark.parametrize("surface", ["responses.create", "messages.create"])
@pytest.mark.parametrize("mode", ["success", "missing_usage", "outage"])
def test_native_compression_reserves_before_dispatch_and_preserves_unknown_billing(
    api, tenant, tmp_path, monkeypatch, surface, mode
):
    base, store, plan, _, _ = prepare(api, tenant, tmp_path, monkeypatch, surface=surface)
    run_id = queue(api, base, plan)
    transport = httpx2 if surface == "messages.create" else httpx
    calls = []

    def handle(request):
        with api[2].state.sessions() as session:
            current = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
            )
            assert current is not None and current.state == "reserved"
        body = json.loads(request.content)
        text = body["input"] if surface == "responses.create" else body["messages"][0]["content"]
        assert "evaluator-only-compression-canary" not in text
        frame = json.loads(text[text.index("{") :])
        calls.append(frame)
        if mode == "outage":
            raise transport.ConnectError("synthetic compression outage")
        response = response_body(surface)
        target = (
            response["output"][0]["content"][0]
            if surface == "responses.create"
            else response["content"][0]
        )
        target["text"] = "14" if "deadline-days=14" in frame["context"] else "wrong"
        response["usage"].update(
            input_tokens=len(text.encode()), output_tokens=2, total_tokens=len(text.encode()) + 2
        )
        if mode == "missing_usage":
            response.pop("usage")
        return transport.Response(200, json=response)

    executor = native_executor(surface, monkeypatch, handle)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        records = list(session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id)))
        assert len(records) == len(calls) == run.requests_used
        assert "deadline-days" not in json.dumps(run.results)
        if mode == "success":
            assert result["status"] == "COMPLETED", result
            choices = {r["candidate"]["key"]: r for r in run.results["recommendations"]}
            assert choices["compression-1"]["compression_automation_eligible"]
            assert not choices["compression-2"]["compression_automation_eligible"]
        else:
            assert result["status"] == "FAILED" and len(calls) == 1
            assert not run.results["billing_complete"] and run.reserved_usd > 0
    assert (
        api[0]
        .post(
            base + f"/experiments/{run_id}/cases/paired",
            headers=tenant[2],
            json={"purpose": "synthetic-review"},
        )
        .status_code
        == 403
    )


def test_compression_draft_scope_and_revocation_never_dispatch(api, tenant, tmp_path, monkeypatch):
    base, store, plan, policy, body = prepare(api, tenant, tmp_path, monkeypatch)
    assert api[0].post(base + "/compression/draft", headers=tenant[2], json=body).status_code == 403
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-compression-scope"})
        .json()["id"]
    )
    assert (
        api[0]
        .post(f"/v1/projects/{other}/compression/draft", headers=api[1], json=body)
        .status_code
        == 404
    )
    run_id = queue(api, base, plan)
    assert api[0].delete(base + f"/capture-policies/{policy}", headers=api[1]).status_code == 200

    def forbidden(request):
        raise AssertionError("REVOKED_CONTENT_CANNOT_DISPATCH")

    executor = native_executor("responses.create", monkeypatch, forbidden)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    assert result["status"] == "FAILED"
    with api[2].state.sessions() as session:
        assert not list(
            session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
        )


def test_cancellation_after_dispatch_keeps_the_late_bill_and_never_replays(
    api, tenant, tmp_path, monkeypatch
):
    base, store, plan, _, _ = prepare(api, tenant, tmp_path, monkeypatch)
    run_id = queue(api, base, plan)
    calls = []
    cancellation_statuses = []

    def handle(request):
        calls.append(1)
        cancelled = api[0].post(base + f"/experiments/{run_id}/cancel", headers=api[1], json={})
        cancellation_statuses.append(cancelled.status_code)
        response = response_body("responses.create")
        response["output"][0]["content"][0]["text"] = "14"
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    assert len(calls) == 1
    assert cancellation_statuses == [200]
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        assert run.status == "CANCELLED" and run.requests_used == run.known_cost_requests == 1
        assert run.spent_usd > 0 and run.reserved_usd > 0


def test_preparation_expiry_has_no_provider_request_or_reservation(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals import compression

    base, store, plan, _, _ = prepare(api, tenant, tmp_path, monkeypatch)
    run_id = queue(api, base, plan)
    started = compression.time.monotonic()
    clock = [started]
    original = compression.compress_context

    def slow(*args, **kwargs):
        clock[0] = started + 301
        return original(*args, **kwargs)

    monkeypatch.setattr(compression.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(compression, "compress_context", slow)

    def forbidden(request):
        raise AssertionError("EXPIRED_COMPRESSION_CANNOT_DISPATCH")

    executor = native_executor("responses.create", monkeypatch, forbidden)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    assert result["status"] == "FAILED"
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        assert run.requests_used == 0 and run.reserved_usd == 0
        assert not list(
            session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
        )


def test_metadata_export_and_project_delete_cover_encrypted_compressed_inputs(
    api, tenant, tmp_path, monkeypatch
):
    base, store, plan, _, _ = prepare(api, tenant, tmp_path, monkeypatch)
    run_id = queue(api, base, plan)

    def handle(request):
        response = response_body("responses.create")
        response["output"][0]["content"][0]["text"] = "14"
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    try:
        outcome = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
    finally:
        executor.close()
    assert outcome["status"] == "COMPLETED"
    with api[2].state.sessions() as session:
        samples = list(
            session.scalars(
                select(PayloadSample).where(
                    PayloadSample.metadata_safe["run_id"].as_string() == str(run_id)
                )
            )
        )
        assert samples
        content = json.loads(store.get(UUID(tenant[0]), samples[0].object_ref))
        assert "rendered_input" in content["compression"]
        assert "deadline-days" in content["case"]["input"]
        refs = [s.object_ref for s in samples]
        metadata = session.get(ExperimentPlanRecord, UUID(plan["id"])).specification
        refs.append(metadata["artifact_ref"])
        slug = session.get(Project, UUID(tenant[0])).slug
    exported = api[0].get(base + "/export", headers=api[1])
    assert exported.status_code == 200 and b"deadline-days" not in exported.content
    assert api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code == 200
    for ref in refs:
        with pytest.raises(OSError):
            store.get(UUID(tenant[0]), ref)
