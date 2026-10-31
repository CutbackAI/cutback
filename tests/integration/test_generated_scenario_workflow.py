"""Real PostgreSQL/native mock transport: current owner review and development data."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset

from cutback.demo_scenarios import synthetic_executor
from cutback.server.db import (
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    ExperimentRunRecord,
    PayloadSample,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.scenario_db import ScenarioVersionRecord
from cutback.server.scenario_worker import run_scenario_one

pytestmark = pytest.mark.postgres


def prepare(api, tenant, tmp_path, monkeypatch):
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    body = {
        "request_id": str(uuid4()),
        "dataset_id": dataset["id"],
        "requirements": {
            "task_brief": "Classify independent synthetic support tasks",
            "coverage_goals": [
                {"key": "concise", "description": "Requests with limited user time"}
            ],
        },
        "generator": {
            "key": "owner-generator",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_input_tokens": 20000,
            "max_output_tokens": 2048,
        },
        "budget": {"max_usd": "0.1", "max_requests": 1},
        "transport": "scripted_test_only",
    }
    return base, store, body


def draft_and_queue(api, base, body):
    client, admin, _ = api
    draft = client.post(base + "/scenarios/drafts", headers=admin, json=body)
    assert draft.status_code == 201, draft.text
    path = base + "/scenarios/" + draft.json()["id"]
    approval = {
        "plan_hash": draft.json()["plan_hash"],
        "approval_ref": "synthetic-owned-generation",
    }
    approved = client.post(path + "/approve-generation", headers=admin, json=approval)
    assert approved.status_code == 200, approved.text
    queued = client.post(path + "/runs", headers=admin, json={"plan_hash": approval["plan_hash"]})
    assert queued.status_code == 202, queued.text
    return path, queued.json()


def generate(api, store, status, **changes):
    executor, calls = synthetic_executor(**changes)
    try:
        result = run_scenario_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=status["generation_run_id"],
        )
    finally:
        executor.close()
    return result, calls


def review(api, path, status, decision="approve", epoch=None):
    return api[0].post(
        path + "/review",
        headers=api[1],
        json={
            "version_sha256": status["version_sha256"],
            "decision": decision,
            "approval_ref": "synthetic-owner-review",
            "expected_review_approval_id": epoch,
        },
    )


def test_generated_scenarios_require_exact_approval_and_private_content_scope(
    api, tenant, tmp_path, monkeypatch
):
    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    client, admin, app = api
    assert client.post(base + "/scenarios/drafts", headers=tenant[2], json=body).status_code == 403
    drafted = client.post(base + "/scenarios/drafts", headers=admin, json=body)
    assert drafted.status_code == 201, drafted.text
    value = drafted.json()
    assert body["requirements"]["task_brief"] not in drafted.text
    path = base + "/scenarios/" + value["id"]
    repeated = client.post(base + "/scenarios/drafts", headers=admin, json=body)
    assert repeated.status_code == 201 and repeated.json()["existing_draft"]
    changed = {**body, "max_training_examples": 1}
    assert client.post(base + "/scenarios/drafts", headers=admin, json=changed).status_code == 409
    assert (
        client.post(
            path + "/runs", headers=admin, json={"plan_hash": value["plan_hash"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/approve-generation",
            headers=admin,
            json={"plan_hash": "a" * 64, "approval_ref": "synthetic"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/content", headers=tenant[2], json={"purpose": "synthetic-review"}
        ).status_code
        == 403
    )
    with app.state.sessions() as session:
        row = session.get(ScenarioVersionRecord, UUID(value["id"]))
        payload = session.get(PayloadSample, row.input_payload_id)
        private = json.loads(store.get(row.project_id, payload.object_ref))["value"]
        frame = json.loads(private["input"])
        assert frame["gold_and_tool_results_supplied"] is False
        assert all("gold" not in case and "reference" not in case for case in frame["training"])
        assert all("gold" in case for case in private["training"].values())


def test_native_scenarios_review_import_family_binding_and_revocation(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.worker import load_dataset

    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    path, queued = draft_and_queue(api, base, body)
    duplicate = api[0].post(path + "/runs", headers=api[1], json={"plan_hash": queued["plan_hash"]})
    assert duplicate.json()["generation_run_id"] == queued["generation_run_id"]
    result, calls = generate(api, store, queued)
    assert result["status"] == "COMPLETED", result
    assert len(calls) == 1 and calls[0]["store"] is False
    content = api[0].post(
        path + "/content", headers=api[1], json={"purpose": "synthetic-owner-review"}
    )
    assert content.status_code == 200, content.text
    value = content.json()
    assert value["preview"]["independent_labels"] is False
    assert value["preview"]["reactive_user_simulation"] is False
    assert value["preview"]["population_coverage"] == "unknown"
    request = {
        "request_id": str(uuid4()),
        "version_sha256": value["version_sha256"],
        "review_approval_id": str(uuid4()),
        "approval_ref": "synthetic-reviewed-import",
    }
    assert api[0].post(path + "/imports", headers=api[1], json=request).status_code == 409
    accepted = review(api, path, value)
    assert accepted.status_code == 200, accepted.text
    request["review_approval_id"] = accepted.json()["review_approval_id"]
    collision = {**request, "request_id": body["dataset_id"]}
    assert api[0].post(path + "/imports", headers=api[1], json=collision).status_code == 409
    imported = api[0].post(path + "/imports", headers=api[1], json=request)
    assert imported.status_code == 201, imported.text
    assert api[0].post(path + "/imports", headers=api[1], json=request).json()["existing_import"]
    with api[2].state.sessions() as session:
        dataset = session.get(DatasetRecord, UUID(imported.json()["dataset_id"]))
        data, _ = load_dataset(session, dataset, store)
        assert {item["split"] for item in data["items"]} == {"validation"}
        assert dataset.provenance["split_counts"] == {"train": 0, "test": 0, "validation": 5}
        items = session.scalars(
            select(DatasetItem).where(DatasetItem.dataset_id == dataset.id)
        ).all()
        assert len({item.lineage_group_id for item in items}) == 4
        run = session.get(ExperimentRunRecord, UUID(queued["generation_run_id"]))
        assert run.requests_used == run.known_cost_requests == 1 and run.spent_usd > 0
        assert (
            len(session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run.id)).all())
            == 1
        )
    rejected = review(api, path, value, "reject", request["review_approval_id"])
    assert rejected.status_code == 200, rejected.text
    with api[2].state.sessions() as session:
        dataset = session.get(DatasetRecord, UUID(imported.json()["dataset_id"]))
        with pytest.raises(ValueError, match="NO_LONGER_AUTHORIZED"):
            load_dataset(session, dataset, store)
    assert api[0].post(path + "/imports", headers=api[1], json=request).status_code == 409
    assert review(api, path, value, "approve", request["review_approval_id"]).status_code == 409
    reapproved = review(api, path, value, "approve", rejected.json()["review_approval_id"])
    assert reapproved.status_code == 200
    with api[2].state.sessions() as session:
        dataset = session.get(DatasetRecord, UUID(imported.json()["dataset_id"]))
        with pytest.raises(ValueError, match="NO_LONGER_AUTHORIZED"):
            load_dataset(session, dataset, store)


@pytest.mark.parametrize("mode", ["invalid", "outage", "unknown_usage"])
def test_uncertain_or_invalid_scenario_dispatch_is_never_replayed(
    api, tenant, tmp_path, monkeypatch, mode
):
    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    path, queued = draft_and_queue(api, base, body)
    result, calls = generate(api, store, queued, **{mode: True})
    assert result["status"] == "FAILED", result
    assert len(calls) == 1
    again, retried = generate(api, store, queued)
    assert again["status"] == "IDLE" and retried == []
    status = api[0].get(path, headers=api[1]).json()
    assert status["request_reservations"] == 1 and status["version_sha256"] is None
    assert status["billing_complete"] is (mode == "invalid")


def test_current_capture_policy_is_rechecked_before_scenario_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    _, queued = draft_and_queue(api, base, body)
    with api[2].state.sessions.begin() as session:
        row = session.get(ScenarioVersionRecord, UUID(queued["id"]))
        session.get(CapturePolicyRecord, row.capture_policy_id).enabled = False
    result, calls = generate(api, store, queued)
    assert result["status"] == "FAILED" and calls == []


def test_scenario_output_consumes_its_exact_shared_quota_reservation(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.evaluation_api import policy_object_count

    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    _, queued = draft_and_queue(api, base, body)
    with api[2].state.sessions.begin() as session:
        row = session.get(ScenarioVersionRecord, UUID(queued["id"]))
        policy = session.get(CapturePolicyRecord, row.capture_policy_id)
        policy.policy = {**policy.policy, "max_samples": 42}
        assert policy_object_count(session, row.capture_policy_id) == 42
    rejected = api[0].post(
        base + "/scenarios/drafts", headers=api[1], json={**body, "request_id": str(uuid4())}
    )
    assert rejected.status_code == 429
    result, calls = generate(api, store, queued)
    assert result["status"] == "COMPLETED" and len(calls) == 1
    with api[2].state.sessions() as session:
        row = session.get(ScenarioVersionRecord, UUID(queued["id"]))
        assert policy_object_count(session, row.capture_policy_id) == 42


def test_generated_scenarios_hide_foreign_project_identifiers(api, tenant, tmp_path, monkeypatch):
    base, _, body = prepare(api, tenant, tmp_path, monkeypatch)
    _, queued = draft_and_queue(api, base, body)
    foreign = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "synthetic-scenario-" + uuid4().hex})
        .json()["id"]
    )
    path = f"/v1/projects/{foreign}/scenarios/{queued['id']}"
    assert api[0].get(path, headers=api[1]).status_code == 404
    assert api[0].get(path, headers=tenant[2]).status_code == 403


def test_rejected_review_between_target_calls_stops_the_next_reservation(
    api, tenant, tmp_path, monkeypatch
):
    from support_evaluation import SyntheticExecutor, plan_for

    from cutback.server.worker import run_one

    base, store, body = prepare(api, tenant, tmp_path, monkeypatch)
    path, queued = draft_and_queue(api, base, body)
    assert generate(api, store, queued)[0]["status"] == "COMPLETED"
    content = (
        api[0]
        .post(path + "/content", headers=api[1], json={"purpose": "synthetic-owner-review"})
        .json()
    )
    accepted = review(api, path, content).json()
    imported = api[0].post(
        path + "/imports",
        headers=api[1],
        json={
            "request_id": str(uuid4()),
            "version_sha256": content["version_sha256"],
            "review_approval_id": accepted["review_approval_id"],
            "approval_ref": "synthetic-development",
        },
    )
    assert imported.status_code == 201, imported.text
    value = imported.json()
    plan = plan_for(
        value,
        split="validation",
        metric={"kind": "exact_label", "minimum": 0.9, "min_test_samples": 1, "min_test_groups": 1},
    )
    planned = api[0].post(
        base + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": value["dataset_id"], "specification": plan.model_dump(mode="json")},
    )
    assert planned.status_code == 201, planned.text
    frozen = planned.json()
    approved = api[0].post(
        base + f"/experiments/plans/{frozen['id']}/approve",
        headers=api[1],
        json={"plan_hash": frozen["plan_hash"], "approval_ref": "synthetic-target-budget"},
    )
    assert approved.status_code == 200, approved.text
    target = (
        api[0].post(base + "/experiments", headers=api[1], json={"plan_id": frozen["id"]}).json()
    )

    class RevokingExecutor(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            result = super().execute(model, text, timeout_seconds)
            with api[2].state.sessions.begin() as session:
                row = session.get(ScenarioVersionRecord, UUID(queued["id"]))
                row.review_status = "REJECTED"
            return result

    executor = RevokingExecutor()
    result = run_one(
        api[2].state.sessions,
        store,
        executor=executor,
        execution_enabled=True,
        only_run_id=target["id"],
    )
    assert result["status"] == "FAILED" and len(executor.calls) == 1
    with api[2].state.sessions() as session:
        run = session.get(ExperimentRunRecord, UUID(target["id"]))
        assert run.requests_used == run.known_cost_requests == 1
