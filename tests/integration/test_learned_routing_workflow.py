"""Canonical training calls, private fitting, independent native validation and holdout."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from support_agent import native_response
from support_native_agent import native_executor

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentRunRecord, PayloadSample
from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def prepared(api, tenant, tmp_path, monkeypatch, *, incomplete=False, surface="responses.create"):
    client, admin, app = api
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    app.state.artifact_store = store
    base = f"/v1/projects/{tenant[0]}"
    provider = "anthropic" if surface == "messages.create" else "openai"
    policy = client.post(
        base + "/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "owned-routing-training",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["routing-task"],
            "allowed_providers": [provider],
            "sample_rate": 1,
            "max_samples": 4000,
            "max_payload_bytes": 512000,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert policy.status_code == 201, policy.text
    policy_id = policy.json()["id"]
    cases = [
        {
            "id": "route-" + str(i),
            "group": "route-family-" + str(i),
            "input": "Owned case "
            + chr(65 + i // 26)
            + chr(65 + i % 26)
            + (" short" if i % 2 else " long" * 25),
            "gold": "A",
        }
        for i in range(120)
    ]
    response = client.post(
        base + "/datasets",
        headers=admin,
        json={
            "workflow": "routing-task",
            "policy_id": policy_id,
            "source_type": "synthetic_fixture",
            "provenance_ref": "independent-routing-synthetic-v1",
            "independent_labels": True,
            "cases": cases,
        },
    )
    assert response.status_code == 201, response.text
    dataset = response.json()
    calls = []

    def handler(request):
        if surface == "messages.create":
            import httpx2

            assert request.url.host == "api.anthropic.com" and request.url.path == "/v1/messages"
            body = json.loads(request.content)
            calls.append(body)
            output = "A"
            if (
                body["model"] == "claude-haiku-4-5"
                and len(body["messages"][-1]["content"].encode()) > 100
            ):
                output = "B"
            return httpx2.Response(
                200,
                json={
                    "id": "msg_owned_" + uuid4().hex,
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "content": [{"type": "text", "text": output}],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 2,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            )
        assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
        body = json.loads(request.content)
        calls.append(body)
        result = native_response(request).json()
        if body["model"] == "gpt-4.1-mini" and len(body["input"].encode()) > 100:
            result["output"][0]["content"][0]["text"] = "B"
            if incomplete:
                result["status"] = "incomplete"
                result["incomplete_details"] = {"reason": "max_output_tokens"}
        return httpx.Response(200, json=result)

    executor = native_executor(surface, monkeypatch, handler)
    executor.synthetic_transport = True
    body = {
        "request_id": str(uuid4()),
        "content_nonce": str(uuid4()),
        "dataset_id": dataset["id"],
        "baseline": {
            "key": "baseline",
            "provider": provider,
            "model": "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4.1",
            "max_output_tokens": 64,
        },
        "candidates": [
            {
                "key": "small",
                "provider": provider,
                "model": "claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini",
                "max_output_tokens": 64,
            }
        ],
        "metric": {
            "kind": "exact_label",
            "minimum": 1,
            "max_regression": 0,
            "min_test_samples": 3,
            "min_test_groups": 3,
        },
        "budget": {"max_usd": "10" if provider == "anthropic" else "2", "max_requests": 600},
        "max_cases": 256,
        "transport": "scripted_test_only",
    }
    return base, store, dataset, policy_id, body, executor, calls


def queued_run(api, base, plan):
    client, admin, app = api
    approved = client.post(
        base + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "owned-synthetic-routing"},
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(
        base + "/experiments",
        headers=admin,
        json={"plan_id": plan["id"]},
    )
    assert queued.status_code == 202, queued.text
    return UUID(queued.json()["id"])


def execute(api, base, store, plan, executor):
    app = api[2]
    run_id = queued_run(api, base, plan)
    result = run_one(
        app.state.sessions,
        store,
        executor=executor,
        execution_enabled=True,
        only_run_id=run_id,
    )
    assert result["status"] == "COMPLETED", result.get("results", {}).get("code")
    return result


def test_fixture_only_training_is_blocked_by_a_live_worker_before_any_request(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, body, executor, calls = prepared(api, tenant, tmp_path, monkeypatch)
    client, admin, app = api
    try:
        created = client.post(base + "/routing/training-drafts", headers=admin, json=body)
        assert created.status_code == 201, created.text
        run_id = queued_run(api, base, created.json())
        executor.synthetic_transport = False
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] == "BLOCKED" and not calls
        with app.state.sessions() as session:
            assert not session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
        executor.synthetic_transport = True
        resumed = client.post(base + f"/experiments/{run_id}/recheck", headers=admin, json={})
        assert resumed.status_code == 200, resumed.text
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] == "COMPLETED" and calls
    finally:
        executor.close()


def test_incomplete_native_training_retains_unknown_cost_without_automatic_replay(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, body, executor, calls = prepared(
        api, tenant, tmp_path, monkeypatch, incomplete=True
    )
    client, admin, app = api
    try:
        created = client.post(base + "/routing/training-drafts", headers=admin, json=body)
        assert created.status_code == 201, created.text
        run_id = queued_run(api, base, created.json())
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] == "FAILED"
        count = len(calls)
        with app.state.sessions() as session:
            run = session.get(ExperimentRunRecord, run_id)
            rows = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert run.status == "FAILED" and run.results["billing_complete"] is False
            assert any(row.spent_usd is None for row in rows)
            assert all(row.reserved_usd > 0 for row in rows)
        run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert len(calls) == count
        fit = client.post(
            base + "/routing/fits",
            headers=admin,
            json={
                "request_id": str(uuid4()),
                "training_run_id": str(run_id),
                "purpose": "owned-uncertainty-check",
            },
        )
        assert fit.status_code == 409 and len(calls) == count
    finally:
        executor.close()


@pytest.mark.parametrize("surface", ["responses.create", "messages.create"])
def test_native_training_fitting_validation_and_untouched_confirmation(
    api, tenant, tmp_path, monkeypatch, surface
):
    base, store, dataset, policy_id, body, executor, calls = prepared(
        api, tenant, tmp_path, monkeypatch, surface=surface
    )
    client, admin, app = api
    try:
        assert (
            client.post(base + "/routing/training-drafts", headers=tenant[2], json=body).status_code
            == 403
        )
        response = client.post(base + "/routing/training-drafts", headers=admin, json=body)
        assert response.status_code == 201, response.text
        plan = response.json()
        assert plan["fits_budget"], {
            "samples": plan["sample_n"],
            "modeled_maximum_usd": plan["worst_case_modeled_usd"],
            "requests": plan["requests"],
        }
        assert not calls
        training = execute(api, base, store, plan, executor)
        assert training["results"]["recommendations"] == []
        assert training["results"]["routing"]["heldout_quality_claim"] is False
        with app.state.sessions() as session:
            receipts = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == UUID(training["run_id"]))
            ).all()
            assert receipts and all(
                row.purpose == "optimization" and row.spent_usd is not None for row in receipts
            )
        fitting = {
            "request_id": str(uuid4()),
            "training_run_id": training["run_id"],
            "options": {"min_leaf_cases": 1, "min_training_groups": 3},
            "purpose": "owned-routing-review",
        }
        from cutback.server.routing_service import authenticated_training

        with app.state.sessions() as session:
            authenticated, *_ = authenticated_training(
                session, store, UUID(tenant[0]), UUID(training["run_id"])
            )
            assert authenticated.source_run_id == UUID(training["run_id"])
        response = client.post(base + "/routing/fits", headers=admin, json=fitting)
        assert response.status_code == 201, response.text
        fitted = response.json()
        count = len(calls)
        assert client.post(base + "/routing/fits", headers=admin, json=fitting).json() == fitted
        assert len(calls) == count
        changed = {**fitting, "purpose": "different-owned-intent"}
        assert client.post(base + "/routing/fits", headers=admin, json=changed).status_code == 409
        assert client.get(base + "/routing/fits?limit=1", headers=admin).json() == [fitted]
        assert client.get(base + "/routing/fits?limit=1&offset=1", headers=admin).json() == []
        assert client.get(base + "/routing/fits?limit=101", headers=admin).status_code == 422
        other = client.post(
            "/v1/projects", headers=admin, json={"slug": "routing-other-" + uuid4().hex}
        ).json()["id"]
        other_base = f"/v1/projects/{other}"
        assert (
            client.post(other_base + "/routing/fits", headers=admin, json=fitting).status_code
            == 409
        )
        assert client.get(other_base + "/routing/fits", headers=tenant[2]).status_code == 403
        assert not client.get(other_base + "/routing/fits", headers=admin).json()
        assert len(calls) == count
        fit_path = base + "/routing/fits/" + fitted["id"]
        assert (
            client.post(
                fit_path + "/content", headers=tenant[2], json={"purpose": "owned-review"}
            ).status_code
            == 403
        )
        private = client.post(
            fit_path + "/content", headers=admin, json={"purpose": "owned-review"}
        )
        assert private.status_code == 200, private.text
        fit = private.json()["content"]["fit"]
        assert (
            fit["fitted_training_native_cost_difference_usd"] != "0"
            and fit["routing_hosting_cost_usd"] is None
        )
        assert fit["source_patch_eligible"] is False
        draft = {
            "request_id": str(uuid4()),
            "content_nonce": str(uuid4()),
            "budget": {"max_usd": "2", "max_requests": 600},
            "purpose": "owned-validation",
        }
        response = client.post(fit_path + "/validation-drafts", headers=admin, json=draft)
        assert response.status_code == 201, response.text
        validation_plan = response.json()
        private_plan = client.post(
            base + f"/experiments/plans/{validation_plan['id']}/content",
            headers=admin,
            json={"purpose": "owned-binding-negative"},
        ).json()["specification"]
        private_plan["routing_execution"]["fit_sha256"] = "a" * 64
        assert (
            client.post(
                base + "/experiments/plans",
                headers=admin,
                json={"dataset_id": dataset["id"], "specification": private_plan},
            ).status_code
            == 409
        )
        assert len(calls) == count
        validation = execute(api, base, store, validation_plan, executor)
        assert validation["results"]["recommendations"][0]["quality_gate_passed"] is True
        confirm = {
            "request_id": str(uuid4()),
            "validation_run_id": validation["run_id"],
            "budget": {"max_usd": "2", "max_requests": 600},
            "purpose": "owned-confirmation",
        }
        response = client.post(fit_path + "/confirmation-drafts", headers=admin, json=confirm)
        assert response.status_code == 201, response.text
        confirmed = execute(api, base, store, response.json(), executor)
        assert confirmed["results"]["lineage"]["fresh_test_claim"] is True
        assert confirmed["results"]["recommendations"][0]["quality_gate_passed"] is True
        assert confirmed["results"]["routing"]["automatic_application_change"] is False
        assert (
            client.delete(base + "/capture-policies/" + policy_id, headers=admin).status_code == 200
        )
        assert (
            client.post(
                fit_path + "/content", headers=admin, json={"purpose": "revoked"}
            ).status_code
            != 200
        )
    finally:
        executor.close()


@pytest.mark.parametrize(
    "damage",
    ["missing", "cost", "uncertain", "not_training", "holdout", "redacted", "expired", "price"],
)
def test_fit_refuses_unverifiable_or_nontraining_source(api, tenant, tmp_path, monkeypatch, damage):
    base, store, dataset, _, body, executor, calls = prepared(api, tenant, tmp_path, monkeypatch)
    client, admin, app = api
    try:
        response = client.post(base + "/routing/training-drafts", headers=admin, json=body)
        assert response.status_code == 201, response.text
        training = execute(api, base, store, response.json(), executor)
        with app.state.sessions.begin() as session:
            run = session.get(ExperimentRunRecord, UUID(training["run_id"]))
            first = session.scalar(
                select(ExecutionCall).where(ExecutionCall.run_id == run.id).limit(1)
            )
            if damage in {"missing", "redacted", "expired"}:
                sample = session.scalar(
                    select(PayloadSample)
                    .where(PayloadSample.metadata_safe["run_id"].as_string() == str(run.id))
                    .limit(1)
                )
                if damage == "missing":
                    session.delete(sample)
                elif damage == "expired":
                    sample.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    sample.metadata_safe = {**sample.metadata_safe, "semantics_preserved": False}
            elif damage == "cost":
                first.spent_usd += 1
            elif damage == "uncertain":
                first.state = "uncertain"
            elif damage == "price":
                first.price_sha256 = "a" * 64
            elif damage == "not_training":
                run.status = "FAILED"
            else:
                from cutback.server.db import DatasetItem

                item = session.scalar(
                    select(DatasetItem)
                    .where(
                        DatasetItem.dataset_id == UUID(dataset["id"]), DatasetItem.split == "train"
                    )
                    .limit(1)
                )
                item.split = "test"
        count = len(calls)
        response = client.post(
            base + "/routing/fits",
            headers=admin,
            json={
                "request_id": str(uuid4()),
                "training_run_id": training["run_id"],
                "options": {"min_leaf_cases": 1},
                "purpose": "owned-invalid-source-check",
            },
        )
        assert response.status_code == 409, response.text
        assert len(calls) == count
    finally:
        executor.close()


def test_new_test_assignment_of_an_existing_training_family_denies_before_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.db import DatasetItem

    base, store, dataset, policy, body, executor, calls = prepared(
        api, tenant, tmp_path, monkeypatch
    )
    client, admin, app = api
    try:
        planned = client.post(base + "/routing/training-drafts", headers=admin, json=body)
        assert planned.status_code == 201, planned.text
        with app.state.sessions() as session:
            item = session.scalar(
                select(DatasetItem)
                .where(DatasetItem.dataset_id == UUID(dataset["id"]), DatasetItem.split == "train")
                .limit(1)
            )
            sample = session.get(PayloadSample, item.payload_id)
            case = json.loads(store.get(UUID(tenant[0]), sample.object_ref))
            family = item.lineage_group_id
        imported = client.post(
            base + "/datasets",
            headers=admin,
            json={
                "workflow": "routing-task",
                "policy_id": policy,
                "source_type": "synthetic_fixture",
                "provenance_ref": "owned-test-reimport",
                "independent_labels": True,
                "cases": [case],
            },
        )
        assert imported.status_code == 201, imported.text
        with app.state.sessions() as session:
            heldout = session.scalar(
                select(DatasetItem).where(DatasetItem.dataset_id == UUID(imported.json()["id"]))
            )
            assert heldout.split == "test" and heldout.lineage_group_id == family
        run_id = queued_run(api, base, planned.json())
        result = run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )
        assert result["status"] == "FAILED" and not calls
        with app.state.sessions() as session:
            assert not session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
    finally:
        executor.close()
