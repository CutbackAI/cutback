"""Expiry, export/deletion and evidence retention preserve uncertain native bills."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from integration.test_routing_program_api import prepared_program
from integration.test_routing_program_ledger import owned_ledger
from sqlalchemy import select

from cutback.evals.experiments import Execution
from cutback.server.db import ExperimentRunRecord, Project
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.routing_program_db import RoutingProgramRecord
from cutback.server.routing_program_retention import expire_programs, retain_programs
from cutback.server.task_economics import retain_task_outcomes

pytestmark = pytest.mark.postgres


def test_expiry_stops_children_but_unknown_bill_and_snapshot_survive_retention(
    api, tenant, tmp_path, monkeypatch
):
    ledger, identifier = owned_ledger(api, tenant, tmp_path, monkeypatch)
    call = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(call, None, "uncertain")
    future = datetime.now(UTC) + timedelta(days=2)
    assert expire_programs(ledger.sessions, now=future, only_program_id=identifier)["expired"] == 1

    def selected(column):
        return column == UUID(tenant[0])

    with ledger.sessions.begin() as session:
        parent = session.get(RoutingProgramRecord, identifier)
        snapshot_id = parent.snapshot_id
        assert parent.status == "EXPIRED" and parent.requests_used == 1
        assert session.get(ExperimentRunRecord, ledger.run_id).status == "CANCELLED"
        retain_programs(session, future, selected)
        retain_task_outcomes(session, future, selected)
        assert session.get(RoutingProgramRecord, identifier) is not None
        assert session.get(TaskCostSnapshot, snapshot_id) is not None
    ledger.record(call, Execution("not persisted", Decimal("0.0002"), 1), "completed")
    with ledger.sessions.begin() as session:
        parent = session.get(RoutingProgramRecord, identifier)
        assert parent.status == "EXPIRED" and parent.spent_usd == Decimal("0.0002")
        retain_programs(session, future, selected)
    with ledger.sessions() as session:
        assert session.get(RoutingProgramRecord, identifier) is None


def test_program_metadata_export_and_project_delete_include_every_new_relation(
    api, tenant, tmp_path, monkeypatch
):
    base, _, store, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    exported = api[0].get(base + "/export", headers=api[1])
    assert exported.status_code == 200
    assert "routing_improvement_programs" in exported.text
    assert "Classify the declared" not in exported.text
    assert (
        api[0]
        .post(base + "/routing/programs/" + row["id"] + "/cancel", headers=api[1], json={})
        .status_code
        == 200
    )
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(base, headers=api[1], params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    with api[2].state.sessions() as session:
        assert not session.scalar(
            select(RoutingProgramRecord.id).where(
                RoutingProgramRecord.project_id == UUID(tenant[0])
            )
        )
