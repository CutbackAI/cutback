"""Real PostgreSQL scope, data rights and one-use criteria-generation approvals."""

import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset

from cutback.server.criteria_db import CriteriaVersionRecord
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, JobRecord, PayloadSample
from cutback.server.execution_db import ExecutionCall

pytestmark = pytest.mark.postgres


def prepared(api, tenant, tmp_path, monkeypatch):
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    body = {
        "dataset_id": dataset["id"],
        "requirements": {
            "task_brief": "Owned support classification",
            "minimum": 0.9,
            "min_test_samples": 3,
            "min_test_groups": 3,
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


def test_criteria_draft_is_encrypted_unapproved_and_queue_is_idempotent(
    api, tenant, tmp_path, monkeypatch
):
    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    client, admin, application = api
    assert client.post(base + "/criteria/drafts", headers=tenant[2], json=body).status_code == 403
    result = client.post(base + "/criteria/drafts", headers=admin, json=body)
    assert result.status_code == 201, result.text
    drafted = result.json()
    assert drafted["provider_calls"] == 0 and drafted["state"] == "PENDING"
    assert "Owned support classification" not in result.text
    path = base + "/criteria/" + drafted["id"]
    assert (
        client.post(
            path + "/runs", headers=admin, json={"plan_hash": drafted["plan_hash"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            path + "/approve-generation",
            headers=admin,
            json={"plan_hash": "a" * 64, "approval_ref": "owned-synthetic"},
        ).status_code
        == 409
    )
    approved = client.post(
        path + "/approve-generation",
        headers=admin,
        json={"plan_hash": drafted["plan_hash"], "approval_ref": "owned-synthetic"},
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(path + "/runs", headers=admin, json={"plan_hash": drafted["plan_hash"]})
    assert queued.status_code == 202, queued.text
    repeated = client.post(path + "/runs", headers=admin, json={"plan_hash": drafted["plan_hash"]})
    assert repeated.status_code == 202 and repeated.json()["existing_run"] is True
    assert repeated.json()["generation_run_id"] == queued.json()["generation_run_id"]
    with application.state.sessions() as session:
        row = session.get(CriteriaVersionRecord, UUID(drafted["id"]))
        plan = session.get(ExperimentPlanRecord, row.generation_plan_id)
        run = session.get(ExperimentRunRecord, row.generation_run_id)
        jobs = session.scalars(select(JobRecord).where(JobRecord.run_id == run.id)).all()
        assert len(jobs) == 1 and jobs[0].execution_kind == "criteria_generation"
        assert plan.approval_status == "consumed" and run.requests_used == 0
        assert "Owned support classification" not in str(plan.specification)
        sample = session.get(PayloadSample, row.input_payload_id)
        private = store.get(row.project_id, sample.object_ref)
        assert b"Owned support classification" in private
        assert b"training-1" in private and b"source_item_ids" in private
        assert not any(
            path.read_bytes().find(b"Owned support classification") >= 0
            for path in store.root.rglob("*")
            if path.is_file()
        )


def queue_generation(api, base, body):
    client, admin, _ = api
    drafted = client.post(base + "/criteria/drafts", headers=admin, json=body)
    assert drafted.status_code == 201, drafted.text
    value = drafted.json()
    path = base + "/criteria/" + value["id"]
    approved = client.post(
        path + "/approve-generation",
        headers=admin,
        json={"plan_hash": value["plan_hash"], "approval_ref": "owned-synthetic-generator"},
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(path + "/runs", headers=admin, json={"plan_hash": value["plan_hash"]})
    assert queued.status_code == 202, queued.text
    return path, queued.json()


def test_concurrent_generation_queue_and_review_have_one_canonical_identity(
    api, tenant, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from cutback.demo_criteria import synthetic_executor
    from cutback.server.criteria_worker import run_criteria_one

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    drafted = api[0].post(base + "/criteria/drafts", headers=api[1], json=body).json()
    path = base + "/criteria/" + drafted["id"]
    assert (
        api[0]
        .post(
            path + "/approve-generation",
            headers=api[1],
            json={"plan_hash": drafted["plan_hash"], "approval_ref": "owned-concurrent-generation"},
        )
        .status_code
        == 200
    )

    def queue_once(_):
        return api[0].post(path + "/runs", headers=api[1], json={"plan_hash": drafted["plan_hash"]})

    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(queue_once, range(3)))
    assert all(response.status_code == 202 for response in responses)
    identities = {response.json()["generation_run_id"] for response in responses}
    assert len(identities) == 1
    executor, calls = synthetic_executor()
    try:
        run_id = UUID(next(iter(identities)))
        assert (
            run_criteria_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "COMPLETED"
        )
        assert len(calls) == 1
        current = api[0].get(path, headers=api[1]).json()

        def decide(decision):
            return api[0].post(
                path + "/review",
                headers=api[1],
                json={
                    "version_sha256": current["version_sha256"],
                    "decision": decision,
                    "approval_ref": "owned-concurrent-review",
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(decide, ["approve", "reject"]))
        assert sorted(response.status_code for response in decisions) == [200, 409]
        repeated = queue_once(0)
        assert (
            repeated.json()["generation_run_id"] == str(run_id)
            and repeated.json()["existing_run"] is True
        )
        assert (
            run_criteria_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
        assert len(calls) == 1
    finally:
        executor.close()


def test_crashed_generation_call_cannot_be_replayed(api, tenant, tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from cutback.server.criteria_service import generation_authority, load_generation
    from cutback.server.criteria_worker import run_criteria_one
    from cutback.server.execution_ledger import ExecutionLedger

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    path, queued = queue_generation(api, base, body)
    run_id = UUID(queued["generation_run_id"])
    with api[2].state.sessions.begin() as session:
        row = session.get(CriteriaVersionRecord, UUID(queued["id"]))
        run = session.get(ExperimentRunRecord, run_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        planned = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_generation(store, row.project_id, planned.specification)
        row.state = run.status = job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(minutes=2)
        environment, job_id = row.environment_id, job.id
    from cutback.evals.experiments import upper_cost

    def authorize(session):
        generation_authority(
            session,
            store,
            session.get(CriteriaVersionRecord, UUID(queued["id"])),
            synthetic_transport=True,
        )

    ledger = ExecutionLedger(
        api[2].state.sessions, run_id, job_id, plan, environment, authorize=authorize
    )
    ledger.reserve(upper_cost(plan.baseline), plan.baseline, "optimization")
    with api[2].state.sessions.begin() as session:
        job = session.get(JobRecord, job_id)
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        call = session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
        call.lease_until = job.lease_until

    class Forbidden:
        synthetic_transport = True

        def execute(self, *args, **kwargs):
            raise AssertionError("CRASHED_CRITERIA_CALL_CANNOT_REPLAY")

    result = run_criteria_one(
        api[2].state.sessions,
        store,
        executor=Forbidden(),
        execution_enabled=True,
        only_run_id=run_id,
    )
    assert result["status"] == "IDLE"
    public = api[0].get(path, headers=api[1]).json()
    assert (
        public["state"] == "FAILED"
        and public["request_reservations"] == 1
        and public["billing_complete"] is False
    )
    repeated = api[0].post(path + "/runs", headers=api[1], json={"plan_hash": queued["plan_hash"]})
    assert repeated.status_code == 202 and repeated.json()["generation_run_id"] == str(run_id)
    with api[2].state.sessions() as session:
        assert (
            session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id)).state
            == "uncertain"
        )


@pytest.mark.parametrize("when", ["before_dispatch", "after_generation"])
def test_expired_private_criteria_content_never_reconstructs_an_approval(
    api, tenant, tmp_path, monkeypatch, when
):
    from datetime import UTC, datetime, timedelta

    from cutback.demo_criteria import synthetic_executor
    from cutback.server.criteria_worker import run_criteria_one

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    path, queued = queue_generation(api, base, body)
    executor, calls = synthetic_executor()
    try:
        if when == "after_generation":
            assert (
                run_criteria_one(
                    api[2].state.sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=UUID(queued["generation_run_id"]),
                )["status"]
                == "COMPLETED"
            )
        public = api[0].get(path, headers=api[1]).json()
        with api[2].state.sessions.begin() as session:
            row = session.get(CriteriaVersionRecord, UUID(queued["id"]))
            payload = session.get(
                PayloadSample,
                row.output_payload_id if when == "after_generation" else row.input_payload_id,
            )
            payload.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        if when == "before_dispatch":
            assert (
                run_criteria_one(
                    api[2].state.sessions,
                    store,
                    executor=executor,
                    execution_enabled=True,
                    only_run_id=UUID(queued["generation_run_id"]),
                )["status"]
                == "FAILED"
            )
            assert not calls
        else:
            assert len(calls) == 1
            rejected = api[0].post(
                path + "/review",
                headers=api[1],
                json={
                    "version_sha256": public["version_sha256"],
                    "decision": "approve",
                    "approval_ref": "owned-expired-output-negative",
                },
            )
            assert rejected.status_code == 409
        assert (
            api[0]
            .post(path + "/content", headers=api[1], json={"purpose": "owned-expiry-negative"})
            .status_code
            == 409
        )
    finally:
        executor.close()


def test_reimport_cannot_turn_a_known_test_family_into_generator_training(
    api, tenant, tmp_path, monkeypatch
):
    from support_evaluation import dataset_body

    from cutback.evals.datasets import prepare_dataset
    from cutback.server.db import DatasetRecord
    from cutback.server.worker import load_dataset

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    with api[2].state.sessions() as session:
        original = session.get(DatasetRecord, UUID(body["dataset_id"]))
        dataset, _ = load_dataset(session, original, store)
        policy_id = original.provenance["policy_id"]
    protected = {row["case"]["input"] for row in dataset["items"] if row["split"] == "test"}
    request = dataset_body(40).model_copy(update={"policy_id": policy_id})
    seed = next(
        seed
        for seed in range(1, 20)
        if any(
            row["split"] == "train" and row["case"]["input"] in protected
            for row in prepare_dataset(request.model_copy(update={"split_seed": seed}))["items"]
        )
    )
    imported = api[0].post(
        base + "/datasets",
        headers=api[1],
        json=request.model_copy(update={"split_seed": seed}).model_dump(mode="json"),
    )
    assert imported.status_code == 201, imported.text
    rejected = api[0].post(
        base + "/criteria/drafts",
        headers=api[1],
        json={**body, "dataset_id": imported.json()["id"]},
    )
    assert rejected.status_code == 422


def test_criteria_output_slot_is_shared_with_capture_and_released_on_cancellation(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.db import CapturePolicyRecord, DatasetRecord
    from cutback.server.evaluation_api import policy_object_count

    base, _, body = prepared(api, tenant, tmp_path, monkeypatch)
    with api[2].state.sessions.begin() as session:
        source = session.get(DatasetRecord, UUID(body["dataset_id"]))
        policy_id = UUID(source.provenance["policy_id"])
        policy = session.get(CapturePolicyRecord, policy_id)
        existing = policy_object_count(session, policy_id)
        policy.policy = {**policy.policy, "max_samples": existing + 1}
    assert api[0].post(base + "/criteria/drafts", headers=api[1], json=body).status_code == 429
    with api[2].state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, policy_id)
        policy.policy = {**policy.policy, "max_samples": existing + 2}
    created = api[0].post(base + "/criteria/drafts", headers=api[1], json=body)
    assert created.status_code == 201, created.text
    with api[2].state.sessions() as session:
        assert policy_object_count(session, policy_id) == existing + 2
    assert api[0].post(base + "/criteria/drafts", headers=api[1], json=body).status_code == 429
    cancelled = api[0].post(
        base + "/criteria/" + created.json()["id"] + "/cancel",
        headers=api[1],
        json={"reason": "owned-unused-slot"},
    )
    assert cancelled.status_code == 200
    with api[2].state.sessions() as session:
        assert policy_object_count(session, policy_id) == existing + 1


def test_revoked_generation_approver_prevents_native_dispatch(api, tenant, tmp_path, monkeypatch):
    from support_native_agent import native_executor

    from cutback.server.criteria_worker import run_criteria_one

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    drafted = api[0].post(base + "/criteria/drafts", headers=api[1], json=body).json()
    path = base + "/criteria/" + drafted["id"]
    key = api[0].post(base + "/keys", headers=api[1], json={"role": "project_admin"}).json()
    headers = {"Authorization": "Bearer " + key["token"]}
    assert (
        api[0]
        .post(
            path + "/approve-generation",
            headers=headers,
            json={"plan_hash": drafted["plan_hash"], "approval_ref": "owned-revocable-approver"},
        )
        .status_code
        == 200
    )
    queued = api[0].post(path + "/runs", headers=api[1], json={"plan_hash": drafted["plan_hash"]})
    assert queued.status_code == 202
    assert api[0].delete(base + "/keys/" + key["key_id"], headers=api[1]).status_code == 200
    calls = []

    def forbidden(request):
        calls.append(1)
        raise AssertionError("REVOKED_APPROVER_CANNOT_DISPATCH")

    executor = native_executor("responses.create", monkeypatch, forbidden)
    executor.synthetic_transport = True
    try:
        result = run_criteria_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued.json()["generation_run_id"]),
        )
        assert result["status"] == "FAILED" and not calls
    finally:
        executor.close()
    assert api[0].get(path, headers=api[1]).json()["request_reservations"] == 0


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "invalid",
        "missing_usage",
        "outage",
        "incomplete",
        "cancelled_after_dispatch",
        "revoked_after_dispatch",
        "storage_failure",
    ],
)
def test_native_criteria_generator_reserves_once_never_sees_test_rows_and_requires_review(
    api, tenant, tmp_path, monkeypatch, mode
):
    from support_native_agent import native_executor, response_body

    from cutback.server.criteria_worker import run_criteria_one
    from cutback.server.db import DatasetRecord
    from cutback.server.worker import load_dataset

    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    path, queued = queue_generation(api, base, body)
    run_id = UUID(queued["generation_run_id"])
    with api[2].state.sessions() as session:
        dataset, _ = load_dataset(
            session, session.get(DatasetRecord, UUID(body["dataset_id"])), store
        )
    training_inputs = {row["case"]["input"] for row in dataset["items"] if row["split"] == "train"}
    test_inputs = {row["case"]["input"] for row in dataset["items"] if row["split"] == "test"}
    calls = []

    def handle(request):
        with api[2].state.sessions() as session:
            intent = session.scalar(select(ExecutionCall).where(ExecutionCall.run_id == run_id))
            assert intent and intent.state == "reserved" and intent.purpose == "optimization"
        raw = json.loads(request.content)
        frame = json.loads(raw["input"])
        sent = {item["input"] for item in frame["training"]}
        assert sent <= training_inputs and not sent & test_inputs
        assert all(item["anchor"].startswith("training-") for item in frame["training"])
        calls.append(raw)
        if mode == "outage":
            raise httpx.ReadTimeout("OWNED_PRIVATE_CRITERIA_OUTAGE")
        if mode == "cancelled_after_dispatch":
            cancelled = api[0].post(
                path + "/cancel", headers=api[1], json={"reason": "owned-late-cancellation"}
            )
            assert cancelled.status_code == 200
        if mode == "revoked_after_dispatch":
            with api[2].state.sessions() as session:
                policy_id = session.get(CriteriaVersionRecord, UUID(queued["id"])).capture_policy_id
            assert (
                api[0]
                .delete(base + "/capture-policies/" + str(policy_id), headers=api[1])
                .status_code
                == 200
            )
        if mode == "storage_failure":

            def unavailable(*args, **kwargs):
                raise OSError("OWNED_PRIVATE_STORAGE_FAILURE")

            monkeypatch.setattr(store, "put", unavailable)
        proposal = {
            "criteria": [
                {
                    "description": "OWNED_PRIVATE_CRITERION_DESCRIPTION",
                    "metric_kind": "exact_label",
                    "output_field": None,
                    "schema_definition": None,
                    "training_anchors": ["training-1"],
                }
            ]
        }
        if mode == "invalid":
            proposal["criteria"][0]["minimum"] = 0.1
        text = json.dumps(proposal)
        response = response_body("responses.create")
        response["model"] = raw["model"]
        response["output"][0]["content"][0]["text"] = text
        count = len(raw["input"].encode()) + len(raw["instructions"].encode())
        response["usage"].update(
            input_tokens=count,
            output_tokens=len(text.encode()),
            total_tokens=count + len(text.encode()),
        )
        if mode == "missing_usage":
            response.pop("usage")
        if mode == "incomplete":
            response["status"] = "incomplete"
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    executor.synthetic_transport = True
    try:
        result = run_criteria_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        expected = (
            "COMPLETED"
            if mode == "valid"
            else "CANCELLED"
            if mode == "cancelled_after_dispatch"
            else "FAILED"
        )
        assert result["status"] == expected, result
        assert len(calls) == 1
        assert (
            run_criteria_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )["status"]
            == "IDLE"
        )
    finally:
        executor.close()
    public = api[0].get(path, headers=api[1]).json()
    assert public["request_reservations"] == 1 and public["review_status"] == "PENDING"
    assert "OWNED_PRIVATE" not in json.dumps(public)
    assert public["billing_complete"] is (mode not in {"missing_usage", "outage", "incomplete"})
    assert (
        api[0]
        .post(path + "/content", headers=tenant[2], json={"purpose": "viewer-negative"})
        .status_code
        == 403
    )
    if mode == "valid":
        content = api[0].post(path + "/content", headers=api[1], json={"purpose": "owned-review"})
        assert content.status_code == 200, content.text
        assert (
            "OWNED_PRIVATE_CRITERION_DESCRIPTION" in content.text
            and content.json()["criteria_binding"] is None
        )
        reviewed = api[0].post(
            path + "/review",
            headers=api[1],
            json={
                "version_sha256": public["version_sha256"],
                "decision": "approve",
                "approval_ref": "owned-metric-review",
            },
        )
        assert reviewed.status_code == 200, reviewed.text
        private = (
            api[0]
            .post(path + "/content", headers=api[1], json={"purpose": "owned-approved-criteria"})
            .json()
        )
        assert (
            private["criteria_binding"]["review_approval_id"]
            == reviewed.json()["review_approval_id"]
        )
        assert private["criteria"][0]["metric"]["minimum"] == body["requirements"]["minimum"]
        validate_and_confirm(api, tenant, monkeypatch, base, store, body, path, private)
    else:
        assert public["version_sha256"] is None


def validate_and_confirm(api, tenant, monkeypatch, base, store, body, path, private):
    from support_native_agent import native_executor, response_body

    from cutback.server.worker import run_one

    client, admin, application = api
    target = {
        "dataset_id": body["dataset_id"],
        "criteria_binding": private["criteria_binding"],
        "baseline": {
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1",
            "max_input_tokens": 8000,
            "max_output_tokens": 32,
        },
        "candidates": [
            {
                "key": "compact",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 8000,
                "max_output_tokens": 32,
            }
        ],
        "budget": {"max_usd": "1", "max_requests": 200},
    }
    assert (
        client.post(path + "/evaluation-draft", headers=tenant[2], json=target).status_code == 403
    )
    changed = {
        **target,
        "criteria_binding": {**private["criteria_binding"], "version_sha256": "a" * 64},
    }
    assert client.post(path + "/evaluation-draft", headers=admin, json=changed).status_code == 409
    drafted = client.post(path + "/evaluation-draft", headers=admin, json=target)
    assert drafted.status_code == 201, drafted.text
    calls = []

    def handle(request):
        raw = json.loads(request.content)
        calls.append(raw)
        response = response_body("responses.create")
        response["model"] = raw["model"]
        response["output"][0]["content"][0]["text"] = "A"
        response["usage"].update(input_tokens=40, output_tokens=1, total_tokens=41)
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    executor.synthetic_transport = True

    def execute(planned):
        approved = client.post(
            base + "/experiments/plans/" + planned["id"] + "/approve",
            headers=admin,
            json={
                "plan_hash": planned["plan_hash"],
                "approval_ref": "owned-criteria-target-budget",
            },
        )
        assert approved.status_code == 200, approved.text
        queued = client.post(base + "/experiments", headers=admin, json={"plan_id": planned["id"]})
        assert queued.status_code == 202, queued.text
        result = run_one(
            application.state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=UUID(queued.json()["id"]),
        )
        assert result["status"] == "COMPLETED", result.get("results", {}).get(
            "code", result["status"]
        )
        assert result["results"]["criteria_binding"] == private["criteria_binding"]
        cost = result["results"]["criteria_generation_cost"]
        assert cost["shared_one_time_setup"] is True and cost["billing_complete"] is True
        assert cost["included_in_target_evaluation_spend"] is False
        assert cost["amortized_per_task_usd"] is None and cost["full_task_economics_known"] is False
        assert "OWNED_PRIVATE" not in json.dumps(result)
        return result

    try:
        validation = execute(drafted.json())
        decisions = validation["results"]["recommendations"]
        assert (
            len(decisions) == 1
            and decisions[0]["quality_gate_passed"]
            and decisions[0]["cheaper_on_sample"]
        )
        confirmed = client.post(
            path + "/confirmation",
            headers=admin,
            json={
                "validation_run_id": validation["run_id"],
                "candidate_key": "compact",
                "budget": target["budget"],
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        confirmation = execute(confirmed.json())
        assert confirmation["results"]["lineage"]["fresh_test_claim"] is True
        assert len({r["input"] for r in calls}) == len(calls) // 2
        repeated = client.post(
            path + "/confirmation",
            headers=admin,
            json={
                "validation_run_id": validation["run_id"],
                "candidate_key": "compact",
                "budget": target["budget"],
            },
        )
        assert repeated.status_code == 409
        pending = client.post(path + "/evaluation-draft", headers=admin, json=target)
        assert pending.status_code == 201, pending.text
        assert (
            client.post(
                base + "/experiments/plans/" + pending.json()["id"] + "/approve",
                headers=admin,
                json={
                    "plan_hash": pending.json()["plan_hash"],
                    "approval_ref": "owned-pending-revocation-check",
                },
            ).status_code
            == 200
        )
        revoke = client.post(
            path + "/review",
            headers=admin,
            json={
                "version_sha256": private["version_sha256"],
                "decision": "reject",
                "approval_ref": "owned-review-revocation",
                "expected_review_approval_id": private["review_approval_id"],
            },
        )
        assert revoke.status_code == 200, revoke.text
        assert (
            client.post(
                base + "/experiments", headers=admin, json={"plan_id": pending.json()["id"]}
            ).status_code
            == 409
        )
        assert (
            client.post(path + "/evaluation-draft", headers=admin, json=target).status_code == 409
        )
        renewed = client.post(
            path + "/review",
            headers=admin,
            json={
                "version_sha256": private["version_sha256"],
                "decision": "approve",
                "approval_ref": "owned-explicit-new-review-epoch",
                "expected_review_approval_id": revoke.json()["review_approval_id"],
            },
        )
        assert (
            renewed.status_code == 200
            and renewed.json()["review_approval_id"] != private["review_approval_id"]
        )
        assert (
            client.post(
                base + "/experiments", headers=admin, json={"plan_id": pending.json()["id"]}
            ).status_code
            == 409
        )
        assert (
            client.post(path + "/evaluation-draft", headers=admin, json=target).status_code == 409
        )
        exported = client.get(base + "/export", headers=admin)
        assert (
            exported.status_code == 200
            and "evaluation_criteria_versions" in exported.text
            and "evaluation_criteria_uses" in exported.text
        )
        assert "OWNED_PRIVATE" not in exported.text
        from cutback.server.db import Project

        with application.state.sessions() as session:
            slug = session.get(Project, tenant[0]).slug
            retained = session.scalars(
                select(PayloadSample).where(PayloadSample.project_id == tenant[0])
            ).all()
            refs = [row.object_ref for row in retained]
        assert refs
        deleted = client.delete(base, headers=admin, params={"confirm_slug": slug})
        assert deleted.status_code == 200, deleted.text
        for ref in refs:
            with pytest.raises((FileNotFoundError, ValueError)):
                store.get(tenant[0], ref)
    finally:
        executor.close()
