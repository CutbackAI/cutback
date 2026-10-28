from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_execution_ledger import ledger_for

from cutback.evals.datasets import digest
from cutback.evals.experiments import Execution
from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.db import (
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
)
from cutback.server.execution_ledger import ExecutionLedger

pytestmark = pytest.mark.postgres


def parent_for(ledger, *, requests=1, usd="0.001"):
    """Financial ledger fixture; deliberately no assertion of a completed campaign journey."""
    with ledger.sessions.begin() as session:
        run = session.get(ExperimentRunRecord, ledger.run_id)
        plan = session.get(ExperimentPlanRecord, run.plan_id)
        dataset = session.get(DatasetRecord, plan.dataset_id)
        payload = session.scalar(
            select(PayloadSample).where(PayloadSample.project_id == run.project_id)
        )
        parent = CampaignRecord(
            project_id=run.project_id,
            environment_id=ledger.environment_id,
            request_id=uuid4(),
            dataset_id=dataset.id,
            policy_id=payload.policy_id,
            payload_id=payload.id,
            request_sha256="a" * 64,
            approval_sha256="b" * 64,
            specification={"budget": {"max_usd": usd, "max_requests": requests}},
            economic_envelope={
                "eligible": True,
                "effective_max_usd": usd,
                "max_requests": requests,
            },
            status="WAITING_VALIDATION",
            approval_status="consumed",
            approved_by="bootstrap_admin",
            started_at=datetime.now(UTC),
            deadline_at=datetime.now(UTC) + timedelta(minutes=1),
            expires_at=datetime.now(UTC) + timedelta(days=1),
            actor="bootstrap_admin",
        )
        session.add(parent)
        session.flush()
        session.add(
            CampaignRun(
                project_id=run.project_id,
                environment_id=ledger.environment_id,
                campaign_id=parent.id,
                run_id=run.id,
                phase="validation",
                step_key="validation-r1",
                round_index=1,
                campaign_sha256=parent.approval_sha256,
                plan_sha256=digest(ledger.plan.model_dump(mode="json")),
                derivation={},
            )
        )
        return parent.id


def second_child(ledger, parent_id):
    with ledger.sessions.begin() as session:
        original = session.get(ExperimentRunRecord, ledger.run_id)
        original_plan = session.get(ExperimentPlanRecord, original.plan_id)
        plan = ExperimentPlanRecord(
            project_id=original.project_id,
            dataset_id=original_plan.dataset_id,
            specification=deepcopy(original_plan.specification),
            approval_status="consumed",
            approved_by=original_plan.approved_by,
        )
        session.add(plan)
        session.flush()
        run = ExperimentRunRecord(
            project_id=original.project_id, plan_id=plan.id, status="RUNNING", results={}
        )
        session.add(run)
        session.flush()
        job = JobRecord(
            project_id=original.project_id,
            run_id=run.id,
            status="RUNNING",
            lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        session.add(job)
        session.flush()
        session.add(
            CampaignRun(
                project_id=run.project_id,
                environment_id=ledger.environment_id,
                campaign_id=parent_id,
                run_id=run.id,
                phase="validation",
                step_key="validation-r2",
                round_index=2,
                campaign_sha256="b" * 64,
                plan_sha256=digest(ledger.plan.model_dump(mode="json")),
                derivation={},
            )
        )
        return ExecutionLedger(
            ledger.sessions,
            run.id,
            job.id,
            ledger.plan,
            ledger.environment_id,
            authorize=lambda session: None,
        )


def test_parent_last_slot_is_atomic_across_two_child_ledgers(api, tenant, tmp_path, monkeypatch):
    first = ledger_for(api, tenant, tmp_path, monkeypatch)
    parent_id = parent_for(first)
    second = second_child(first, parent_id)

    def attempt(ledger):
        try:
            return ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [first, second]))
    assert sum(r == "CAMPAIGN_BUDGET_EXHAUSTED" for r in results) == 1
    with first.sessions() as session:
        parent = session.get(CampaignRecord, parent_id)
        assert parent.requests_used == 1 and parent.reserved_usd == Decimal("0.001")
        assert (
            sum(
                session.get(ExperimentRunRecord, child.run_id).requests_used
                for child in (first, second)
            )
            == 1
        )


def test_cancelled_parent_preserves_idempotent_late_cost_without_new_authority(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    parent_id = parent_for(ledger, requests=2, usd="0.002")
    call = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(call, None, "uncertain")
    with ledger.sessions.begin() as session:
        parent = session.get(CampaignRecord, parent_id)
        parent.status = "CANCELLED"
        parent.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
        parent.approved_by = "revoked-principal"
    observed = Execution("synthetic-no-content", Decimal("0.0002"), 3, 4, 5)
    ledger.record(call, observed, "completed")
    ledger.record(call, observed, "completed")
    with ledger.sessions() as session:
        parent = session.get(CampaignRecord, parent_id)
        assert parent.status == "CANCELLED" and parent.known_cost_requests == 1
        assert parent.spent_usd == Decimal("0.0002") and parent.requests_used == 1
    with pytest.raises(ValueError, match="CAMPAIGN_CANCELLED"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")


@pytest.mark.parametrize("change", ["expired", "revoked", "wrong_hash", "over_owner_cap"])
def test_current_parent_authority_and_envelope_checked_before_call(
    api, tenant, tmp_path, monkeypatch, change
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    parent_id = parent_for(ledger)
    with ledger.sessions.begin() as session:
        parent = session.get(CampaignRecord, parent_id)
        if change == "expired":
            parent.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "revoked":
            parent.approved_by = "revoked-principal"
        elif change == "wrong_hash":
            parent.approval_sha256 = "c" * 64
        elif change == "over_owner_cap":
            parent.economic_envelope = {
                "eligible": True,
                "effective_max_usd": "1",
                "max_requests": 1,
            }
    with pytest.raises(ValueError, match="CAMPAIGN"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    with ledger.sessions() as session:
        assert session.get(CampaignRecord, parent_id).requests_used == 0
        assert session.get(ExperimentRunRecord, ledger.run_id).requests_used == 0


def test_legacy_evaluation_approval_cannot_be_reused_for_a_proposer(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="PURPOSE_OUTSIDE_APPROVED_OPERATION"):
        ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "optimization")
    with ledger.sessions() as session:
        assert session.get(ExperimentRunRecord, ledger.run_id).requests_used == 0
