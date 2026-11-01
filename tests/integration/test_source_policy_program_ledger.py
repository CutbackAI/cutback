"""Real source-bound child calls share atomic parent slots and late-cost accounting."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from integration.test_source_policy_program_api import prepared_program
from sqlalchemy import select

from cutback.evals.experiments import Execution, upper_cost
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.plan_artifacts import load_plan
from cutback.server.policy_program_db import SourcePolicyProgramRecord, SourcePolicyProgramRun
from cutback.server.policy_program_service import authorize_program_run
from cutback.server.policy_program_worker import advance_one
from cutback.server.policy_service import execution_payloads

pytestmark = pytest.mark.postgres


def owned_ledger(api, tenant, tmp_path, monkeypatch):
    base, store, _, _, executor, _, _, parent, _ = prepared_program(
        api, tenant, tmp_path, monkeypatch
    )
    executor.close()
    response = api[0].post(
        base + "/source-policy-programs/" + parent["id"] + "/approve",
        headers=api[1],
        json={"program_hash": parent["program_hash"], "approval_ref": "owned-ledger-fixture"},
    )
    assert response.status_code == 200, response.text
    sessions, identifier = api[2].state.sessions, UUID(parent["id"])
    result = advance_one(
        sessions,
        store,
        execution_enabled=True,
        only_program_id=identifier,
        synthetic_transport=True,
    )
    assert result["status"] == "WAITING_STATIC", result
    with sessions.begin() as session:
        link = session.scalar(
            select(SourcePolicyProgramRun).where(SourcePolicyProgramRun.program_id == identifier)
        )
        run = session.get(ExperimentRunRecord, link.run_id)
        planned = session.get(ExperimentPlanRecord, run.plan_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run.id))
        plan = load_plan(store, run.project_id, planned.specification)
        payloads = execution_payloads(
            session, store, run.project_id, plan.source_policy_execution, planned.dataset_id
        )
        job.status = run.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(minutes=5)
        run_id, job_id, environment = run.id, job.id, link.environment_id
    ledger = ExecutionLedger(
        sessions,
        run_id,
        job_id,
        plan,
        environment,
        authorize=lambda session: authorize_program_run(
            session, store, run_id, synthetic_transport=True
        ),
        source_policy_payloads=payloads,
    )
    return ledger, identifier


def test_shared_last_request_slot_is_atomic_for_the_exact_native_leaf(
    api, tenant, tmp_path, monkeypatch
):
    ledger, identifier = owned_ledger(api, tenant, tmp_path, monkeypatch)
    target = ledger.approved_targets()[0]
    with ledger.sessions.begin() as session:
        parent = session.get(SourcePolicyProgramRecord, identifier)
        prior = parent.economic_envelope["max_requests"] - 1
        parent.requests_used = parent.known_cost_requests = prior

    def reserve():
        try:
            return ledger.reserve(upper_cost(target), target, "evaluation")
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    calls = [value for value in results if isinstance(value, UUID)]
    assert len(calls) == 1 and "POLICY_PROGRAM_BUDGET_EXHAUSTED" in results
    ledger.record(calls[0], None, "uncertain")
    with ledger.sessions() as session:
        parent = session.get(SourcePolicyProgramRecord, identifier)
        call = session.get(ExecutionCall, calls[0])
        assert parent.requests_used == parent.economic_envelope["max_requests"]
        assert parent.known_cost_requests == prior and call.state == "uncertain"
        assert parent.reserved_usd == upper_cost(target) and call.spent_usd is None


def test_late_known_receipt_settles_once_after_cancellation_without_reviving_work(
    api, tenant, tmp_path, monkeypatch
):
    ledger, identifier = owned_ledger(api, tenant, tmp_path, monkeypatch)
    target = ledger.approved_targets()[0]
    call = ledger.reserve(upper_cost(target), target, "evaluation")
    ledger.record(call, None, "uncertain")
    with ledger.sessions.begin() as session:
        parent = session.get(SourcePolicyProgramRecord, identifier)
        parent.status, parent.deadline_at = "CANCELLED", datetime.now(UTC) - timedelta(seconds=1)
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
    execution = Execution("synthetic private output", Decimal("0.000123456789"), 1)
    with localcontext() as context:
        context.prec = 6
        ledger.record(call, execution, "completed")
        ledger.record(call, execution, "completed")
    with ledger.sessions() as session:
        parent = session.get(SourcePolicyProgramRecord, identifier)
        assert parent.status == "CANCELLED" and parent.known_cost_requests == 1
        assert parent.spent_usd == execution.cost_usd and parent.reserved_usd == upper_cost(target)


@pytest.mark.parametrize(
    "change", ["cancel", "expire", "principal", "link", "marker", "partial_marker"]
)
def test_changed_parent_authority_or_orphan_marker_blocks_before_reservation(
    api, tenant, tmp_path, monkeypatch, change
):
    ledger, identifier = owned_ledger(api, tenant, tmp_path, monkeypatch)
    target = ledger.approved_targets()[0]
    with ledger.sessions.begin() as session:
        parent = session.get(SourcePolicyProgramRecord, identifier)
        run = session.get(ExperimentRunRecord, ledger.run_id)
        planned = session.get(ExperimentPlanRecord, run.plan_id)
        if change == "cancel":
            parent.status = "CANCELLED"
        elif change == "expire":
            parent.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "principal":
            parent.approved_by = str(uuid4())
        elif change == "link":
            session.delete(
                session.scalar(
                    select(SourcePolicyProgramRun).where(SourcePolicyProgramRun.run_id == run.id)
                )
            )
        else:
            planned.specification = {
                key: value
                for key, value in planned.specification.items()
                if key
                not in (
                    {"source_policy_program_id"}
                    if change == "partial_marker"
                    else {"source_policy_program_id", "source_policy_program_sha256"}
                )
            }
    with pytest.raises(ValueError, match="POLICY_PROGRAM_"):
        ledger.reserve(upper_cost(target), target, "evaluation")
    with ledger.sessions() as session:
        assert session.get(SourcePolicyProgramRecord, identifier).requests_used == 0
        assert (
            session.scalar(select(ExecutionCall.id).where(ExecutionCall.run_id == ledger.run_id))
            is None
        )
