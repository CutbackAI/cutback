"""Shared parent limits are atomic; missing ownership never permits a paid replay."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_execution_ledger import ledger_for

from cutback.evals.experiments import Execution
from cutback.server.db import (
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
)
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.execution_db import ExecutionCall
from cutback.server.routing_program_db import RoutingProgramRecord, RoutingProgramRun
from cutback.server.routing_program_service import approval_hash

pytestmark = pytest.mark.postgres


def owned_ledger(api, tenant, tmp_path, monkeypatch):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = uuid4()
    with ledger.sessions.begin() as session:
        run = session.get(ExperimentRunRecord, ledger.run_id)
        planned = session.get(ExperimentPlanRecord, run.plan_id)
        dataset = session.get(DatasetRecord, planned.dataset_id)
        sample = session.scalar(
            select(PayloadSample).where(PayloadSample.project_id == run.project_id)
        )
        snapshot = TaskCostSnapshot(
            project_id=run.project_id,
            environment_id=ledger.environment_id,
            snapshot_id=uuid4(),
            request_sha256="a" * 64,
            evidence_sha256="b" * 64,
            specification={},
            report={},
            actor="bootstrap_admin",
        )
        session.add(snapshot)
        session.flush()
        parent = RoutingProgramRecord(
            id=identifier,
            project_id=run.project_id,
            environment_id=ledger.environment_id,
            request_id=uuid4(),
            dataset_id=dataset.id,
            template_plan_id=planned.id,
            snapshot_id=snapshot.id,
            policy_id=sample.policy_id,
            payload_id=sample.id,
            request_sha256="c" * 64,
            template_plan_sha256=planned.specification["plan_hash"],
            snapshot_sha256=snapshot.evidence_sha256,
            approval_sha256="0" * 64,
            specification={"budget": {"max_usd": "1", "max_requests": 2}},
            economic_envelope={
                "contract": "cutback.routing-program-native-budget.v1",
                "eligible": True,
                "effective_max_usd": "0.001",
                "max_requests": 1,
            },
            status="WAITING_STATIC",
            approval_status="consumed",
            approved_by="bootstrap_admin",
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            actor="bootstrap_admin",
        )
        parent.approval_sha256 = approval_hash(parent, parent.request_sha256)
        session.add(parent)
        session.flush()
        planned.specification = {
            **planned.specification,
            "routing_program_id": str(identifier),
            "routing_program_sha256": parent.approval_sha256,
        }
        session.add(
            RoutingProgramRun(
                project_id=run.project_id,
                environment_id=ledger.environment_id,
                program_id=identifier,
                run_id=run.id,
                phase="static_validation",
                program_sha256=parent.approval_sha256,
                plan_sha256=planned.specification["plan_hash"],
                derivation={},
            )
        )
    return ledger, identifier


def test_shared_last_slot_is_atomic_and_uncertain_cost_stays_reserved(
    api, tenant, tmp_path, monkeypatch
):
    ledger, parent_id = owned_ledger(api, tenant, tmp_path, monkeypatch)

    def attempt():
        try:
            return ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    calls = [value for value in outcomes if isinstance(value, UUID)]
    assert len(calls) == 1
    assert "ROUTING_PROGRAM_BUDGET_EXHAUSTED" in outcomes
    ledger.record(calls[0], None, "uncertain")
    with ledger.sessions() as session:
        parent = session.get(RoutingProgramRecord, parent_id)
        run = session.get(ExperimentRunRecord, ledger.run_id)
        assert parent.requests_used == run.requests_used == 1
        assert parent.reserved_usd == run.reserved_usd == Decimal("0.001")
        assert parent.known_cost_requests == run.known_cost_requests == 0
        assert session.get(ExecutionCall, calls[0]).state == "uncertain"


def test_late_bill_repairs_parent_once_without_reviving_cancelled_work(
    api, tenant, tmp_path, monkeypatch
):
    ledger, parent_id = owned_ledger(api, tenant, tmp_path, monkeypatch)
    call = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(call, None, "uncertain")
    with ledger.sessions.begin() as session:
        parent = session.get(RoutingProgramRecord, parent_id)
        parent.status = "CANCELLED"
        parent.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
    result = Execution("private output", Decimal("0.000123456789"), 1)
    with localcontext() as context:
        context.prec = 6
        ledger.record(call, result, "completed")
        ledger.record(call, result, "completed")
    with ledger.sessions() as session:
        parent = session.get(RoutingProgramRecord, parent_id)
        assert parent.status == "CANCELLED"
        assert parent.known_cost_requests == 1 and parent.spent_usd == result.cost_usd
        assert parent.reserved_usd == Decimal("0.001")


@pytest.mark.parametrize(
    "change", ["cancel", "expire", "principal", "envelope", "link", "marker", "partial_orphan"]
)
def test_changed_authority_or_orphan_owner_stops_before_reserving(
    api, tenant, tmp_path, monkeypatch, change
):
    ledger, parent_id = owned_ledger(api, tenant, tmp_path, monkeypatch)
    with ledger.sessions.begin() as session:
        parent = session.get(RoutingProgramRecord, parent_id)
        run = session.get(ExperimentRunRecord, ledger.run_id)
        if change == "cancel":
            parent.status = "CANCELLED"
        elif change == "expire":
            parent.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "principal":
            parent.approved_by = str(uuid4())
        elif change == "envelope":
            parent.economic_envelope = {**parent.economic_envelope, "max_requests": 9999}
        elif change in {"link", "partial_orphan"}:
            link = session.scalar(
                select(RoutingProgramRun).where(RoutingProgramRun.run_id == run.id)
            )
            session.delete(link)
            if change == "partial_orphan":
                planned = session.get(ExperimentPlanRecord, run.plan_id)
                planned.specification = {
                    key: value
                    for key, value in planned.specification.items()
                    if key != "routing_program_id"
                }
        else:
            planned = session.get(ExperimentPlanRecord, run.plan_id)
            planned.specification = {
                key: value
                for key, value in planned.specification.items()
                if key != "routing_program_id"
            }
    with pytest.raises(ValueError, match="ROUTING_PROGRAM_"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions() as session:
        assert session.get(RoutingProgramRecord, parent_id).requests_used == 0
        assert not session.scalar(
            select(ExecutionCall.id).where(ExecutionCall.run_id == ledger.run_id)
        )
