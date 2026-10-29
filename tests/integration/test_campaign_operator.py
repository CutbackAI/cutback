"""The finite operator uses real native workers, PostgreSQL and one approved envelope."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Inexact, Rounded, localcontext
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_campaign_workflow import advance, approve, child, native, prepare

from cutback.campaign_operator import CampaignOperator
from cutback.server.campaign_db import CampaignRecord
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.proposal_worker import load_proposal, run_proposal_one

pytestmark = pytest.mark.postgres


def operator(api, tenant, store, row, executor=None, **kwargs):
    return CampaignOperator(
        api[2].state.sessions,
        store,
        project_id=tenant[0],
        campaign_id=row["id"],
        campaign_hash=row["campaign_hash"],
        executor=executor,
        **kwargs,
    )


@pytest.mark.parametrize("static_worse", [False, True])
@pytest.mark.parametrize("hostile_context", [False, True])
def test_operator_finishes_the_same_approved_campaign_and_resumes_terminal_without_calls(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    static_worse,
    hostile_context,
):
    base, store, row, _ = prepare(
        api, tenant, tmp_path, monkeypatch, version="cutback.improvement-campaign.v2"
    )
    approve(api, base, row)
    executor, requests = native(monkeypatch, static_worse=static_worse)
    try:
        runner = operator(api, tenant, store, row, executor)
        events = []
        with localcontext() as context:
            if hostile_context:
                context.prec = 3
                context.rounding = ROUND_DOWN
                context.traps[Inexact] = context.traps[Rounded] = True
            final = runner.run(poll_seconds=0.1, emit=events.append)
            if hostile_context:
                assert context.prec == 3 and context.rounding == ROUND_DOWN
                assert context.traps[Inexact] and context.traps[Rounded]
        assert final["status"] == "COMPLETED", final
        assert final["campaign_id"] == row["id"] and final["campaign_hash"] == row["campaign_hash"]
        assert (
            final["cumulative_requests"] == final["cumulative_known_cost_requests"] == len(requests)
        )
        assert final["local_child_attempts"] == (4 if static_worse else 2)
        assert sum("text" in request for request in requests) == int(static_worse)
        before = len(requests)
        assert runner.run()["status"] == "COMPLETED"
        assert len(requests) == before and events[-1]["status"] == "COMPLETED"
        detail = api[0].get(base + f"/campaigns/{row['id']}", headers=api[1]).json()
        assert detail["billing_complete"] and not detail["summary"]["deployment_authorized"]
        assert len(detail["runs"]) == (4 if static_worse else 2)
    finally:
        executor.close()


def test_local_operation_bound_resumes_without_another_approval_or_budget(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    base, store, row, _ = prepare(
        api, tenant, tmp_path, monkeypatch, version="cutback.improvement-campaign.v2"
    )
    approve(api, base, row)
    executor, requests = native(monkeypatch)
    try:
        runner = operator(api, tenant, store, row, executor)
        paused = runner.run(max_operations=1, poll_seconds=0.1)
        assert paused["operator_code"] == "LOCAL_OPERATION_BOUND_REACHED"
        assert paused["status"] == "WAITING_CONFIRMATION"
        assert paused["local_child_attempts"] == 1 and len(requests) > 0
        deadline = paused["deadline_at"]
        complete = runner.run(poll_seconds=0.1)
        assert complete["status"] == "COMPLETED"
        assert complete["deadline_at"] == deadline
        assert complete["campaign_hash"] == row["campaign_hash"]
        assert complete["cumulative_requests"] == len(requests)
    finally:
        executor.close()


def test_operator_does_not_approve_or_cross_scope_and_source_preflight_stops_execution(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    base, store, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    executor, requests = native(monkeypatch)
    try:
        runner = operator(api, tenant, store, row, executor)
        assert runner.run()["operator_code"] == "APPROVAL_REQUIRED"
        for changed in ({"project_id": uuid4()}, {"campaign_hash": "0" * 64}):
            scope = dict(
                project_id=tenant[0], campaign_id=row["id"], campaign_hash=row["campaign_hash"]
            )
            scope.update(changed)
            denied = CampaignOperator(api[2].state.sessions, store, executor=executor, **scope)
            with pytest.raises(ValueError, match="SCOPE_OR_APPROVAL_CHANGED"):
                denied.run()
        approve(api, base, row)

        def changed_source():
            raise ValueError("SOURCE_CHANGED_ANALYZE_AGAIN")

        guarded = operator(api, tenant, store, row, executor, preflight=changed_source)
        with pytest.raises(ValueError, match="SOURCE_CHANGED"):
            guarded.run()
        assert not requests and guarded.snapshot()["status"] == "QUEUED"
        assert child(api, row, "proposal") is None
        cancelled = api[0].post(base + f"/campaigns/{row['id']}/cancel", headers=api[1], json={})
        assert cancelled.status_code == 200
        assert runner.run()["status"] == "CANCELLED" and not requests
    finally:
        executor.close()


@pytest.mark.parametrize("cutoff_after_wait", [False, True])
def test_operator_recovers_an_uncertain_running_child_without_credentials_or_replay(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    cutoff_after_wait,
):
    from cutback.evals.experiments import upper_cost

    base, store, row, _ = prepare(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        generator={
            "transport": "native_provider",
            "variants_per_round": 1,
            "model": {
                "key": "proposer",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 20000,
                "max_output_tokens": 512,
            },
        },
    )
    approve(api, base, row)
    assert advance(api, store, row, synthetic=False)["status"] == "WAITING_PROPOSAL"
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

    def expire():
        with sessions.begin() as session:
            past = datetime.now(UTC) - timedelta(seconds=1)
            session.get(JobRecord, job_id).lease_until = past
            session.get(ExecutionCall, call_id).lease_until = past
            if cutoff_after_wait:
                session.get(CampaignRecord, UUID(row["id"])).deadline_at = past

    if cutoff_after_wait:
        from types import SimpleNamespace

        clock = {"value": 0.0}

        def wait(seconds):
            expire()
            clock["value"] = 4000.0

        monkeypatch.setattr(
            "cutback.campaign_operator.time",
            SimpleNamespace(monotonic=lambda: clock["value"], sleep=wait),
        )
    else:
        expire()

    def forbidden(*args, **kwargs):
        raise AssertionError("CREDENTIALS_OR_TRANSPORT_MUST_NOT_BE_INITIALIZED")

    monkeypatch.setattr("cutback.evals.executors.OpenAIExecutor", forbidden)
    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", forbidden)
    runner = operator(api, tenant, store, row)
    result = runner.run(poll_seconds=0.1)
    assert result["status"] == "FAILED" and result["cumulative_requests"] == 1
    assert result["cumulative_known_cost_requests"] == 0
    with sessions() as session:
        call = session.get(ExecutionCall, call_id)
        assert call.state == "uncertain" and call.spent_usd is None
    assert runner.run()["status"] == "FAILED"


def test_operator_expiry_is_scoped_to_its_exact_campaign(api, tenant, tmp_path, monkeypatch):
    base, store, target, request = prepare(api, tenant, tmp_path, monkeypatch)
    request["request_id"] = str(uuid4())
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201
    other = response.json()
    for row in (target, other):
        approve(api, base, row)
    with api[2].state.sessions.begin() as session:
        for row in (target, other):
            session.get(CampaignRecord, UUID(row["id"])).deadline_at = datetime.now(
                UTC
            ) - timedelta(seconds=1)
    assert operator(api, tenant, store, target).run()["status"] == "FAILED"
    with api[2].state.sessions() as session:
        assert session.get(CampaignRecord, UUID(other["id"])).status == "QUEUED"


def test_targeted_proposer_recovery_does_not_reap_an_unrelated_expired_child(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    base, store, target, request = prepare(api, tenant, tmp_path, monkeypatch)
    request["request_id"] = str(uuid4())
    response = api[0].post(base + "/campaigns", headers=api[1], json=request)
    assert response.status_code == 201
    other = response.json()
    for row in (target, other):
        approve(api, base, row)
        assert advance(api, store, row)["status"] == "WAITING_PROPOSAL"
    other_id = child(api, other, "proposal")
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == other_id))
        job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    executor, requests = native(monkeypatch)
    try:
        result = run_proposal_one(
            sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=child(api, target, "proposal"),
        )
        assert result["status"] == "COMPLETED" and len(requests) == 1
        with sessions() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.run_id == other_id))
            assert job.status == "RUNNING"
            assert session.get(CampaignRecord, UUID(other["id"])).status == "WAITING_PROPOSAL"
    finally:
        executor.close()
