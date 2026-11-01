"""Reference loss between native calls stops dispatch and preserves known billing."""

from decimal import Decimal
from uuid import UUID

import pytest
from integration.test_source_policy_cached_workflow import execution
from integration.test_source_policy_program_workflow import drive
from sqlalchemy import delete

from cutback.server.policy_activity_db import PolicyActivityRecord
from cutback.server.policy_program_worker import advance_one

pytestmark = pytest.mark.postgres


def test_current_reference_is_revalidated_between_native_calls_without_losing_the_first_bill(
    api, tenant, tmp_path, monkeypatch
):
    store, executor, calls, _, parent, path = execution(api, tenant, tmp_path, monkeypatch)
    original = executor.execute
    removed = []

    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        with api[2].state.sessions() as session:
            value = session.execute(
                delete(PolicyActivityRecord).where(
                    PolicyActivityRecord.project_id == UUID(tenant[0])
                )
            )
            assert value.rowcount == 1
            session.commit()
        removed.append(True)
        return result

    monkeypatch.setattr(executor, "execute", execute)
    try:
        result = drive(api, store, executor, parent, path)
        assert removed == [True] and len(calls) == 1
        assert result["status"] in {"FAILED", "ABSTAINED"}
        assert result["requests"] == result["known_cost_requests"] == 1
        assert result["billing_complete"] is True
        assert Decimal(result["known_spend_subtotal_usd"]) > 0
        assert all(row["phase"] != "confirmation" for row in result["runs"])
        for _ in range(2):
            advance_one(
                api[2].state.sessions,
                store,
                execution_enabled=True,
                only_program_id=parent["id"],
                synthetic_transport=True,
            )
        assert removed == [True] and len(calls) == 1
    finally:
        executor.close()
