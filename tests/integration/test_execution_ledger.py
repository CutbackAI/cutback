from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Context, Decimal, localcontext
from uuid import UUID

import pytest
from sqlalchemy import select
from support_evaluation import SyntheticExecutor, authorized_dataset

from cutback.evals.experiments import Execution
from cutback.server.db import Environment, ExperimentRunRecord, JobRecord
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.plan_artifacts import load_plan
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def queue(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    base, store, _, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    response = client.post(
        base + "/experiments/plans/" + plan["id"] + "/approve",
        headers=admin,
        json={"plan_hash": plan["plan_hash"], "approval_ref": "synthetic-only"},
    )
    assert response.status_code == 200, response.text
    response = client.post(base + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert response.status_code == 202, response.text
    return base, store, UUID(response.json()["id"])


def ledger_for(api, tenant, tmp_path, monkeypatch):
    from cutback.server.db import ExperimentPlanRecord

    _, store, run_id = queue(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, run_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        plan_row = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_plan(store, run.project_id, plan_row.specification)
        environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == run.project_id, Environment.name == "staging"
            )
        )
        job.status = run.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=100)
        job_id = job.id
    return ExecutionLedger(
        sessions, run_id, job_id, plan, environment, authorize=lambda session: None
    )


def test_ambient_rounding_cannot_admit_a_call_above_the_exact_spend_cap(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).reserved_usd = Decimal("0.999000000001")
    with localcontext(Context(prec=10, rounding=ROUND_FLOOR)):
        with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
            ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions() as session:
        assert not session.scalar(
            select(ExecutionCall.id).where(ExecutionCall.run_id == ledger.run_id)
        )


def test_ambient_rounding_cannot_reduce_the_canonical_settled_cost(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).spent_usd = Decimal("0.999000000001")
    with localcontext(Context(prec=10, rounding=ROUND_FLOOR)):
        ledger.record(
            identifier,
            Execution(output="A", cost_usd=Decimal("0.000123456789"), latency_ms=1),
            "completed",
        )
    with ledger.sessions() as session:
        assert session.get(ExperimentRunRecord, ledger.run_id).spent_usd == Decimal(
            "0.999123456790"
        )


def test_worker_call_is_durably_reserved_before_transport_and_cost_is_recorded(
    api, tenant, tmp_path, monkeypatch
):
    _, store, run_id = queue(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions

    class Observed(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            with sessions() as session:
                rows = session.scalars(
                    select(ExecutionCall)
                    .where(ExecutionCall.run_id == run_id)
                    .order_by(ExecutionCall.sequence)
                ).all()
                assert rows[-1].state == "reserved" and rows[-1].spent_usd is None
                assert len(rows) == len(self.calls) + 1
            return super().execute(model, text, timeout_seconds)

    executor = Observed()
    result = run_one(sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id)
    assert result["status"] == "COMPLETED", result
    with sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        calls = session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id)).all()
        assert run.call_ledger_version == 1 and run.known_cost_requests == len(
            executor.calls
        ) == len(calls)
        assert run.spent_usd == sum(c.spent_usd for c in calls)
        assert all(c.state == "completed" for c in calls)


def test_last_request_slot_is_atomic_and_uncertain_call_is_never_replayed(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).requests_used = (
            ledger.plan.budget.max_requests - 1
        )
        session.get(ExperimentRunRecord, ledger.run_id).call_ledger_version = 1
        session.get(ExperimentRunRecord, ledger.run_id).known_cost_requests = 0

    def attempt():
        try:
            return ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    identifiers = [o for o in outcomes if isinstance(o, UUID)]
    assert len(identifiers) == 1 and "BUDGET_EXHAUSTED" in outcomes
    ledger.record(identifiers[0], None, "uncertain")
    with ledger.sessions() as session:
        row = session.get(ExecutionCall, identifiers[0])
        assert row.state == "uncertain" and row.spent_usd is None


def test_idempotent_and_late_usage_preserves_cancelled_run_and_delete_blocks_live_call(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions.begin() as session:
        run = session.get(ExperimentRunRecord, ledger.run_id)
        run.status = "CANCELLED"
        session.get(JobRecord, ledger.job_id).status = "CANCELLED"
        project = run.project_id
    # Cancelling a task cannot make an in-flight vendor request disappear.
    client, admin, _ = api
    from cutback.server.db import Project

    with ledger.sessions() as session:
        slug = session.get(Project, project).slug
    denied = client.delete(f"/v1/projects/{project}", headers=admin, params={"confirm_slug": slug})
    assert denied.status_code == 409 and "IN_FLIGHT_EXECUTION" in denied.text
    ledger.record(identifier, None, "uncertain")
    result = Execution("not retained", Decimal("0.0002"), 2, 4, 3)
    ledger.record(identifier, result, "completed")
    ledger.record(identifier, result, "completed")
    with ledger.sessions() as session:
        run = session.get(ExperimentRunRecord, ledger.run_id)
        assert (
            run.status == "CANCELLED"
            and run.spent_usd == Decimal("0.0002")
            and run.known_cost_requests == 1
        )
        assert session.get(ExecutionCall, identifier).state == "completed_late"
    exported = client.get(f"/v1/projects/{project}/export", headers=admin)
    assert (
        exported.status_code == 200
        and "execution_calls" in exported.text
        and "not retained" not in exported.text
    )


def test_reservation_rejects_unapproved_model_and_current_revocation_before_io(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="CONFIGURATION_OUTSIDE"):
        ledger.reserve(
            Decimal("0.001"),
            ledger.plan.baseline.model_copy(update={"instruction": "changed"}),
            "evaluation",
        )

    def revoke(session):
        raise ValueError("AUTHORITY_REVOKED")

    ledger.authorize = revoke
    with pytest.raises(ValueError, match="AUTHORITY_REVOKED"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions() as session:
        assert session.get(ExperimentRunRecord, ledger.run_id).requests_used == 0


def test_expired_cancelled_call_becomes_unknown_without_request_replay(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.execution_ledger import recover_execution_leases

    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
        session.get(JobRecord, ledger.job_id).status = "CANCELLED"
        session.get(ExecutionCall, identifier).lease_until = datetime.now(UTC) - timedelta(
            seconds=1
        )
    recover_execution_leases(ledger.sessions, only_run_id=ledger.run_id)
    with ledger.sessions() as session:
        assert session.get(ExecutionCall, identifier).state == "uncertain"
        run = session.get(ExperimentRunRecord, ledger.run_id)
        assert run.requests_used == 1 and run.known_cost_requests == 0 and run.status == "CANCELLED"
    with pytest.raises(ValueError, match="CANCELLED_OR_LEASE_EXPIRED"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")


def test_approved_budget_hash_cannot_be_mutated_in_memory(api, tenant, tmp_path, monkeypatch):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    ledger.plan = ledger.plan.model_copy(
        update={"budget": ledger.plan.budget.model_copy(update={"max_requests": 9999})}
    )
    with pytest.raises(ValueError, match="APPROVING_PRINCIPAL"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions() as session:
        assert session.get(ExperimentRunRecord, ledger.run_id).requests_used == 0


def test_lost_reservation_acknowledgement_cannot_claim_complete_billing_or_approve_change(
    api, tenant, tmp_path, monkeypatch
):
    _, store, run_id = queue(api, tenant, tmp_path, monkeypatch)
    original = ExecutionLedger.reserve
    attempts = []

    def lost_once(self, *args):
        identifier = original(self, *args)
        attempts.append(identifier)
        if len(attempts) == 1:
            raise ConnectionError("owned synthetic lost commit acknowledgement")
        return identifier

    monkeypatch.setattr(ExecutionLedger, "reserve", lost_once)
    executor = SyntheticExecutor()
    result = run_one(
        api[2].state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
    )
    assert result["status"] == "FAILED" and executor.calls == [], result
    with api[2].state.sessions() as session:
        row = session.get(ExperimentRunRecord, run_id)
        assert row.requests_used == len(executor.calls) + 1
        assert result["results"]["requests"] == row.requests_used
        assert result["results"]["known_cost_requests"] == len(executor.calls)
        assert not result["results"]["billing_complete"]
        assert result["results"]["spent_modeled_usd"] is None
        assert all(not r["quality_gate_passed"] for r in result["results"]["recommendations"])
