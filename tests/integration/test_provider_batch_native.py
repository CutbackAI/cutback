"""Durable canonical reservations through the actual native SDK and owned HTTP."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from integration.test_provider_batch_workflow import prepare
from sqlalchemy import select

from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.provider_batch_db import ProviderBatchRecord
from cutback.server.provider_batch_worker import run_batch_one

pytestmark = pytest.mark.postgres


def native_adapter(*, lose_submit_ack=False, lose_upload_ack=False):
    from cutback.sample_app.batch_provider import synthetic_batch_adapter

    return synthetic_batch_adapter(
        lambda text: "A", lose_submit_ack=lose_submit_ack, lose_upload_ack=lose_upload_ack
    )


def approved(api, tenant, tmp_path, monkeypatch):
    path, plan, store = prepare(api, tenant, tmp_path, monkeypatch)
    row = (
        api[0]
        .post(
            path + "/provider-batches",
            headers=api[1],
            json={"plan_id": plan["id"], "provider_storage_approved": True},
        )
        .json()
    )
    response = api[0].post(
        path + "/provider-batches/" + row["id"] + "/approve",
        headers=api[1],
        json={"approval_hash": row["approval_hash"], "approval_ref": "owned-batch-approval"},
    )
    assert response.status_code == 202, response.text
    return path, response.json(), store


def step(api, store, identifier, adapter):
    with api[2].state.sessions.begin() as session:
        row = session.get(ProviderBatchRecord, UUID(identifier))
        row.available_at = datetime.now(UTC) - timedelta(seconds=1)
    return run_batch_one(
        api[2].state.sessions,
        store,
        adapter=adapter,
        execution_enabled=True,
        only_batch_id=identifier,
    )


def test_native_batch_reserves_every_item_before_submit_collects_and_deletes_vendor_files(
    api, tenant, tmp_path, monkeypatch
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    try:
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        assert requests == []
        with api[2].state.sessions() as session:
            calls = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == UUID(row["run_id"]))
            ).all()
            assert len(calls) == 32 and all(call.state == "reserved" for call in calls)
            batch = session.get(ProviderBatchRecord, UUID(row["id"]))
            assert all(
                call.provider_batch_id == batch.id
                and call.price_sha256 == batch.specification["price_reference"]["sha256"]
                for call in calls
            )
        for _ in range(24):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "COMPLETED":
                break
        assert result["status"] == "COMPLETED", result
        assert state["submissions"] == 2
        public = api[0].get(path + "/provider-batches/" + row["id"], headers=api[1]).json()
        assert public["state"] == "COMPLETED" and public["report"]["billing_complete"] is True
        assert public["report"]["per_request_latency_ms"] is None
        assert public["report"]["recommendations"] == []
        assert public["report"]["heldout_quality_claim"] is False
        assert len([request for request in requests if request[0] == "DELETE"]) == 4
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == run.known_cost_requests == 32
            assert run.spent_usd > 0 and run.spent_usd < run.reserved_usd
    finally:
        adapter.close()


def test_lost_billable_submit_ack_stops_until_explicit_read_only_reconciliation(
    api, tenant, tmp_path, monkeypatch
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter(lose_submit_ack=True)
    try:
        for _ in range(3):
            result = step(api, store, row["id"], adapter)
        assert result["status"] == "UNCERTAIN"
        assert state["submissions"] == 1
        assert step(api, store, row["id"], adapter)["status"] == "NO_ELIGIBLE_BATCH"
        assert state["submissions"] == 1
        response = api[0].post(
            path + "/provider-batches/" + row["id"] + "/reconcile", headers=api[1], json={}
        )
        assert response.status_code == 200, response.text
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        assert state["submissions"] == 1
        for _ in range(24):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "COMPLETED":
                break
        assert result["status"] == "COMPLETED", result
        assert state["submissions"] == 2
    finally:
        adapter.close()


@pytest.mark.parametrize("hint", [False, True])
def test_lost_upload_ack_reconciles_exact_owned_file_without_repeating_upload(
    api, tenant, tmp_path, monkeypatch, hint
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter(lose_upload_ack=True)
    try:
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        assert state["uploads"] == 1 and state["submissions"] == 0
        assert step(api, store, row["id"], adapter)["status"] == "NO_ELIGIBLE_BATCH"
        with api[2].state.sessions() as session:
            part = session.get(ProviderBatchRecord, UUID(row["id"])).pieces[0]
        body = (
            {"hints": [{"model_key": part["model_key"], "file_id": "file-input0"}]} if hint else {}
        )
        response = api[0].post(
            path + "/provider-batches/" + row["id"] + "/reconcile", headers=api[1], json=body
        )
        assert response.status_code == 200, response.text
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        assert state["uploads"] == 1 and state["submissions"] == 0
        assert ("GET", "/v1/files/file-input0/content") in requests
        for _ in range(24):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "COMPLETED":
                break
        assert result["status"] == "COMPLETED", result
        assert state["uploads"] == state["submissions"] == 2
    finally:
        adapter.close()


def test_recovery_rejects_changed_input_bytes_and_cannot_submit(api, tenant, tmp_path, monkeypatch):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter(lose_upload_ack=True)
    try:
        step(api, store, row["id"], adapter)
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        adapter.download = lambda identifier: [b"foreign content"]
        response = api[0].post(
            path + "/provider-batches/" + row["id"] + "/reconcile", headers=api[1], json={}
        )
        assert response.status_code == 200
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        assert state["uploads"] == 1 and state["submissions"] == 0
    finally:
        adapter.close()


def test_cancel_after_lost_upload_only_recovers_metadata_then_deletes(
    api, tenant, tmp_path, monkeypatch
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter(lose_upload_ack=True)
    try:
        step(api, store, row["id"], adapter)
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        assert (
            api[0]
            .post(path + "/provider-batches/" + row["id"] + "/cancel", headers=api[1], json={})
            .status_code
            == 200
        )
        for _ in range(12):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "CANCELLED":
                break
        assert result["status"] == "CANCELLED", result
        assert state["uploads"] == 1 and state["submissions"] == 0
        assert not any(path.endswith("/content") for _, path in requests)
        assert ("DELETE", "/v1/files/file-input0") in requests
    finally:
        adapter.close()


@pytest.mark.parametrize("revoke", [False, True])
def test_batch_cancellation_records_allowed_late_cost_and_revocation_stops_payload_reads(
    api, tenant, tmp_path, monkeypatch, revoke
):
    from cutback.server.db import CapturePolicyRecord, ExperimentPlanRecord

    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    try:
        for _ in range(3):
            assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        assert state["submissions"] == 1
        if revoke:
            with api[2].state.sessions.begin() as session:
                plan = session.get(ExperimentPlanRecord, UUID(row["plan_id"]))
                policy = session.get(CapturePolicyRecord, UUID(plan.specification["policy_id"]))
                policy.enabled = False
            assert step(api, store, row["id"], adapter)["status"] == "CANCEL_REQUESTED"
        else:
            assert (
                api[0]
                .post(path + "/provider-batches/" + row["id"] + "/cancel", headers=api[1], json={})
                .status_code
                == 200
            )
        for _ in range(16):
            step(api, store, row["id"], adapter)
            current = api[0].get(path + "/provider-batches/" + row["id"], headers=api[1]).json()
            if current["state"] == "CANCELLED":
                break
        assert current["state"] == "CANCELLED", current["report"]
        assert state["submissions"] == 1
        assert current["report"]["billing_complete"] is (not revoke)
        downloads = [request for request in requests if request[1].endswith("/content")]
        assert len(downloads) == (0 if revoke else 1)
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == 32
            assert run.known_cost_requests == (0 if revoke else 32)
        assert len([request for request in requests if request[0] == "DELETE"]) == 2
    finally:
        adapter.close()


def test_generic_experiment_cancel_cannot_leave_a_billable_batch_running(
    api, tenant, tmp_path, monkeypatch
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    try:
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        response = api[0].post(
            path + "/experiments/" + row["run_id"] + "/cancel", headers=api[1], json={}
        )
        assert response.status_code == 200, response.text
        assert step(api, store, row["id"], adapter)["status"] == "CANCELLED"
        assert requests == [] and state["submissions"] == 0
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == run.known_cost_requests == 32
            assert run.spent_usd == 0
            calls = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run.id)
            ).all()
            assert all(call.state == "not_started" and call.spent_usd == 0 for call in calls)
    finally:
        adapter.close()


def test_malformed_duplicate_results_hold_all_charges_and_reconciliation_never_resubmits(
    api, tenant, tmp_path, monkeypatch
):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    original = adapter.download
    try:
        for _ in range(7):
            assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        adapter.download = lambda identifier: original(identifier) * 2
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == 32 and run.known_cost_requests == 0
        assert state["submissions"] == 2
        assert step(api, store, row["id"], adapter)["status"] == "NO_ELIGIBLE_BATCH"
        adapter.download = original
        response = api[0].post(
            path + "/provider-batches/" + row["id"] + "/reconcile", headers=api[1], json={}
        )
        assert response.status_code == 200
        for _ in range(16):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "COMPLETED":
                break
        assert result["status"] == "COMPLETED", result
        assert state["submissions"] == 2
    finally:
        adapter.close()


def test_exhausted_active_wall_budget_cancels_without_upload_or_billable_submission(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.db import ExperimentPlanRecord

    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    try:
        assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        with api[2].state.sessions.begin() as session:
            record = session.get(ProviderBatchRecord, UUID(row["id"]))
            plan = session.get(ExperimentPlanRecord, record.plan_id)
            record.active_elapsed_us = (
                plan.specification["public_specification"]["budget"]["max_wall_seconds"] * 1000000
            )
        assert step(api, store, row["id"], adapter)["status"] == "CANCELLED"
        assert state["submissions"] == 0 and requests == []
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == run.known_cost_requests == 32
            assert run.spent_usd == 0
    finally:
        adapter.close()


def test_crashed_step_keeps_its_active_wall_hold_and_never_repeats_submission(
    api, tenant, tmp_path, monkeypatch
):
    from uuid import uuid4

    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    try:
        for _ in range(3):
            assert step(api, store, row["id"], adapter)["status"] == "RUNNING"
        before = len(requests)
        with api[2].state.sessions.begin() as session:
            record = session.get(ProviderBatchRecord, UUID(row["id"]))
            record.lease_token = uuid4()
            record.lease_until = datetime.now(UTC) - timedelta(seconds=1)
            record.active_reserved_us = 1000000
            record.active_elapsed_us += 1000000
        assert step(api, store, row["id"], adapter)["status"] == "NO_ELIGIBLE_BATCH"
        assert len(requests) == before and state["submissions"] == 1
        response = api[0].get(path + "/provider-batches/" + row["id"], headers=api[1]).json()
        assert response["state"] == "UNCERTAIN"
        assert response["uncertain_active_processing_us"] == 1000000
        assert response["held_active_processing_us"] == 0
        assert response["accounted_active_processing_us"] >= 1000000
    finally:
        adapter.close()


def test_recovery_hint_cannot_cross_model_phase_or_viewer_scope(api, tenant, tmp_path, monkeypatch):
    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter(lose_upload_ack=True)
    endpoint = path + "/provider-batches/" + row["id"] + "/reconcile"
    try:
        step(api, store, row["id"], adapter)
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        assert api[0].post(endpoint, headers=tenant[2], json={}).status_code == 403
        for hint in [
            {"model_key": "foreign", "file_id": "file-input0"},
            {"model_key": "baseline", "batch_id": "batch_foreign"},
            {"model_key": "baseline", "file_id": "file-input0", "batch_id": "batch_foreign"},
        ]:
            assert api[0].post(endpoint, headers=api[1], json={"hints": [hint]}).status_code == 409
        assert (
            api[0]
            .post(
                endpoint,
                headers=api[1],
                json={"hints": [{"model_key": "baseline", "file_id": "https://outside.invalid"}]},
            )
            .status_code
            == 422
        )
        assert state["uploads"] == 1 and state["submissions"] == 0
        assert len(requests) == 1
    finally:
        adapter.close()


def test_interrupted_preparation_cannot_reconcile_or_upload_and_cancels_without_content_reads(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.execution_ledger import ExecutionLedger

    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, state, requests = native_adapter()
    original = ExecutionLedger.reserve

    def interrupted(self, *args):
        original(self, *args)
        raise RuntimeError("owned interruption after durable reservation")

    monkeypatch.setattr(ExecutionLedger, "reserve", interrupted)
    try:
        assert step(api, store, row["id"], adapter)["status"] == "UNCERTAIN"
        assert requests == []
        endpoint = path + "/provider-batches/" + row["id"]
        assert api[0].post(endpoint + "/reconcile", headers=api[1], json={}).status_code == 409
        assert api[0].post(endpoint + "/cancel", headers=api[1], json={}).status_code == 200
        monkeypatch.setattr(
            store,
            "get",
            lambda *args: (_ for _ in ()).throw(AssertionError("no content read authorized")),
        )
        assert step(api, store, row["id"], adapter)["status"] == "CANCELLED"
        with api[2].state.sessions() as session:
            run = session.get(ExperimentRunRecord, UUID(row["run_id"]))
            assert run.requests_used == run.known_cost_requests == 1 and run.spent_usd == 0
        assert requests == [] and state["submissions"] == 0
    finally:
        adapter.close()


def test_metadata_export_and_project_deletion_wait_for_vendor_cleanup(
    api, tenant, tmp_path, monkeypatch
):
    import json

    from cutback.server.db import Project

    path, row, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, _, _ = native_adapter()
    try:
        for _ in range(2):
            step(api, store, row["id"], adapter)
        exported = api[0].get(path + "/export", headers=api[1])
        assert exported.status_code == 200
        entries = [json.loads(line) for line in exported.text.splitlines()]
        records = [item["record"] for item in entries if item["table"] == "provider_batches"]
        assert len(records) == 1 and records[0]["id"] == row["id"]
        with api[2].state.sessions() as session:
            slug = session.get(Project, UUID(tenant[0])).slug
        deletion = path + "?confirm_slug=" + slug
        response = api[0].delete(deletion, headers=api[1])
        assert response.status_code == 409 and "PROVIDER_BATCH_FILES" in response.text
        assert (
            api[0]
            .post(path + "/provider-batches/" + row["id"] + "/cancel", headers=api[1], json={})
            .status_code
            == 200
        )
        for _ in range(12):
            result = step(api, store, row["id"], adapter)
            if result["status"] == "CANCELLED":
                break
        assert result["status"] == "CANCELLED"
        response = api[0].delete(deletion, headers=api[1])
        assert response.status_code == 200, response.text
        assert (
            api[0].get(path + "/provider-batches/" + row["id"], headers=api[1]).status_code == 404
        )
    finally:
        adapter.close()
