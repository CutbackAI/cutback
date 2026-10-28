import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_campaign_workflow import advance, approve, child, native, prepare

from cutback.server.campaign_db import CampaignRecord
from cutback.server.campaign_retention import expire_campaigns
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, JobRecord, Project
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.proposal_worker import load_proposal, run_proposal_one

pytestmark = pytest.mark.postgres


def test_request_replay_conflict_scope_and_metadata_export_delete(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, request = prepare(api, tenant, tmp_path, monkeypatch)
    request["specification"]["budget"]["max_usd"] = "9"
    assert api[0].post(base + "/campaigns", headers=api[1], json=request).status_code == 409
    assert api[0].post(base + "/campaigns", headers=tenant[2], json=request).status_code == 403
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "synthetic-other-" + uuid4().hex})
        .json()["id"]
    )
    assert (
        api[0].get(f"/v1/projects/{other}/campaigns/{row['id']}", headers=api[1]).status_code == 404
    )
    assert (
        api[0].get(f"/v1/projects/{other}/campaigns/{row['id']}", headers=tenant[2]).status_code
        == 403
    )
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    exported = api[0].get(base + "/export", headers=api[1])
    assert exported.status_code == 200
    assert "Return A." not in exported.text and "Classify synthetic" not in exported.text
    tables = {json.loads(line)["table"] for line in exported.text.splitlines()}
    assert {
        "improvement_campaigns",
        "campaign_candidates",
        "campaign_runs",
        "campaign_economic_references",
    } <= tables
    assert (
        api[0].post(base + f"/campaigns/{row['id']}/cancel", headers=api[1], json={}).status_code
        == 200
    )
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(base, headers=api[1], params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    assert not list((store.root / tenant[0]).glob("*"))
    with api[2].state.sessions() as session:
        assert session.get(CampaignRecord, UUID(row["id"])) is None


def test_expiry_stops_campaign_even_with_inference_disabled(api, tenant, tmp_path, monkeypatch):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    with api[2].state.sessions.begin() as session:
        session.get(CampaignRecord, UUID(row["id"])).deadline_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    assert expire_campaigns(api[2].state.sessions)["expired"] >= 1
    report = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
    assert report["status"] == "FAILED" and report["requests"] == 0
    executor, requests = native(monkeypatch)
    try:
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "proposal"),
            )["status"]
            == "IDLE"
        )
        assert not requests
    finally:
        executor.close()


def test_scripted_campaign_cannot_use_provider_key_worker_transport(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    executor, requests = native(monkeypatch)
    executor.synthetic_transport = False
    try:
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "proposal"),
            )["status"]
            == "FAILED"
        )
        assert not requests
        assert api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()["requests"] == 0
    finally:
        executor.close()


def test_crash_after_reservation_is_uncertain_and_never_replayed(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.evals.experiments import upper_cost

    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    approve(api, base, row)
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    sessions = api[2].state.sessions
    run_id = child(api, row, "proposal")
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, run_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        record = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_proposal(store, run.project_id, record.specification)
        run.status = job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=60)
        job_id = job.id
    ledger = ExecutionLedger(
        sessions, run_id, job_id, plan, UUID(row["environment_id"]), authorize=lambda session: None
    )
    call_id = ledger.reserve(upper_cost(plan.baseline), plan.baseline, "optimization")
    with sessions.begin() as session:
        past = datetime.now(UTC) - timedelta(seconds=1)
        session.get(JobRecord, job_id).lease_until = past
        session.get(ExecutionCall, call_id).lease_until = past
    executor, requests = native(monkeypatch)
    try:
        from cutback.server.worker_boot import worker_cycle

        monkeypatch.setattr(
            "cutback.server.worker_boot.configured_execution_enabled", lambda: False
        )
        for flag in (
            "CUTBACK_ONLINE_EXECUTION",
            "CUTBACK_QUALITY_EXECUTION",
            "CUTBACK_TRAINING_EXECUTION",
            "CUTBACK_WEBHOOK_EXECUTION",
        ):
            monkeypatch.setenv(flag, "false")
        recovered = worker_cycle(sessions, store)
        assert recovered["execution_recovery"]["status"] == "OK"
        assert (
            run_proposal_one(
                sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
            )["status"]
            == "IDLE"
        )
        assert not requests
        assert advance(api, store, row)["status"] == "FAILED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert (
            detail["requests"] == 1
            and detail["known_cost_requests"] == 0
            and not detail["billing_complete"]
        )
        with sessions() as session:
            call = session.get(ExecutionCall, call_id)
            assert call.state == "uncertain" and call.spent_usd is None
    finally:
        executor.close()


def test_revoked_approving_principal_stops_before_proposer_transport(
    api, tenant, tmp_path, monkeypatch
):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    key = api[0].post(base + "/keys", headers=api[1], json={"role": "project_admin"}).json()
    approved = api[0].post(
        base + f"/campaigns/{row['id']}/approve",
        headers={"Authorization": "Bearer " + key["token"]},
        json={"campaign_hash": row["campaign_hash"], "approval_ref": "synthetic-scoped-principal"},
    )
    assert approved.status_code == 200, approved.text
    assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    assert api[0].delete(base + "/keys/" + key["key_id"], headers=api[1]).status_code == 200
    executor, requests = native(monkeypatch)
    try:
        assert (
            run_proposal_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=child(api, row, "proposal"),
            )["status"]
            == "FAILED"
        )
        assert not requests
        assert advance(api, store, row)["status"] == "FAILED"
    finally:
        executor.close()
