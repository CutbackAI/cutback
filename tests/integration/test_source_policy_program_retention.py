"""Retention does not erase an unsettled bill or its scoped source-program owner."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from integration.test_source_policy_program_api import prepared_program
from integration.test_source_policy_program_workflow import drive, execution
from sqlalchemy import select

from cutback.server.db import Project
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.execution_db import ExecutionCall
from cutback.server.operations import maintain
from cutback.server.policy_program_db import SourcePolicyProgramRecord
from cutback.server.policy_program_retention import expire_programs, retain_programs
from cutback.server.task_economics import retain_task_outcomes

pytestmark = pytest.mark.postgres


def test_unknown_native_bill_remains_after_full_metadata_retention(
    api, tenant, tmp_path, monkeypatch
):
    _, store, executor, calls, row, path = execution(
        api, tenant, tmp_path, monkeypatch, uncertain=True
    )
    try:
        result = drive(api, store, executor, row, path)
        assert result["status"] == "FAILED" and len(calls) == 1
        sessions, identifier = api[2].state.sessions, UUID(row["id"])
        old, future = datetime.now(UTC) - timedelta(days=10), datetime.now(UTC) + timedelta(days=2)
        with sessions.begin() as session:
            parent = session.get(SourcePolicyProgramRecord, identifier)
            snapshot_id = parent.snapshot_id
            assert parent.requests_used == 1 and parent.known_cost_requests == 0
            parent.created_at = parent.completed_at = old
            call = session.scalar(
                select(ExecutionCall).where(ExecutionCall.project_id == UUID(tenant[0]))
            )
            assert call is not None and call.spent_usd is None
            call_id = call.id
            call.created_at = call.completed_at = old
            call.lease_until = old
        maintain(
            sessions,
            now=future,
            raw_days=1,
            aggregate_days=1,
            apply=True,
            project_id=UUID(tenant[0]),
        )
        with sessions() as session:
            assert session.get(ExecutionCall, call_id) is not None
            assert session.get(SourcePolicyProgramRecord, identifier) is not None
            assert session.get(TaskCostSnapshot, snapshot_id) is not None
    finally:
        executor.close()


def test_expiry_export_and_deletion_cover_the_new_private_relations(
    api, tenant, tmp_path, monkeypatch
):
    base, store, _, _, executor, calls, _, row, _ = prepared_program(
        api, tenant, tmp_path, monkeypatch
    )
    try:
        sessions, identifier = api[2].state.sessions, UUID(row["id"])
        future = datetime.now(UTC) + timedelta(days=2)
        assert expire_programs(sessions, now=future, only_program_id=identifier)["expired"] == 1
        exported = api[0].get(base + "/export", headers=api[1])
        assert exported.status_code == 200
        assert all(
            name in exported.text
            for name in (
                "source_policy_programs",
                "source_policy_program_payloads",
            )
        )
        assert "OWNED_PRIVATE" not in exported.text
        with sessions.begin() as session:
            retain_programs(session, future, lambda column: column == UUID(tenant[0]))
            retain_task_outcomes(session, future, lambda column: column == UUID(tenant[0]))
        with sessions() as session:
            assert session.get(SourcePolicyProgramRecord, identifier) is None
            slug = session.get(Project, UUID(tenant[0])).slug
        deleted = api[0].delete(base, headers=api[1], params={"confirm_slug": slug})
        assert deleted.status_code == 200, deleted.text
        assert calls == []
    finally:
        executor.close()
