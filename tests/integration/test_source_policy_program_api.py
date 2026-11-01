"""One immutable common-policy approval, real PostgreSQL scope and no HTTP inference."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from integration.test_source_policy_execution import prepared
from integration.test_task_outcomes import declaration, environment, ingest, spans

pytestmark = pytest.mark.postgres


def prepared_program(api, tenant, tmp_path, monkeypatch, **changes):
    binding = changes.pop("source_binding", None)
    current_routed = changes.pop("current_routed", False)
    base, store, dataset, policy_id, executor, calls, template, saved, _ = prepared(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        cached=False,
        routed=False,
        binding=binding,
        current_routed=current_routed,
    )
    task = declaration(
        workflow=template.workflow,
        components=[
            {
                "key": "owned-compute",
                "kind": "compute",
                "amount_usd": "1",
            }
        ],
    )
    spans(api, tenant, task)
    assert ingest(api, tenant, task).status_code == 200
    snapshot = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "workflow": template.workflow,
            "expected_tasks": 1,
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    request = {
        "request_id": str(uuid4()),
        "template_plan_id": saved["id"],
        "baseline_snapshot_id": snapshot.json()["id"],
        "budget": {"max_usd": "100", "max_requests": 1000, "max_wall_seconds": 600},
        "conservative_savings_per_success_usd": "0.01",
        "forecast_successful_tasks": 100000,
        **changes,
    }
    response = api[0].post(base + "/source-policy-programs", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    return base, store, dataset, policy_id, executor, calls, template, response.json(), request


def test_prepare_reconcile_one_approval_and_cancel_without_any_provider_calls(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, _, executor, calls, _, row, request = prepared_program(
        api, tenant, tmp_path, monkeypatch
    )
    path = base + "/source-policy-programs/" + row["id"]
    try:
        assert row["status"] == "READY_FOR_APPROVAL" and row["current_round"] == 0
        assert row["economic_envelope"]["eligible"] is True
        repeat = api[0].post(base + "/source-policy-programs", headers=api[1], json=request)
        assert repeat.status_code == 201 and repeat.json()["id"] == row["id"], repeat.text
        changed = api[0].post(
            base + "/source-policy-programs",
            headers=api[1],
            json={**request, "forecast_successful_tasks": 200000},
        )
        assert changed.status_code == 409 and "INTENT_CHANGED" in changed.text
        public = api[0].get(path, headers=tenant[2])
        assert public.status_code == 200 and "OWNED_PRIVATE" not in public.text
        assert "payload_id" not in public.text and "policy_sha256" not in public.text
        assert (
            api[0]
            .post(path + "/content", headers=tenant[2], json={"purpose": "review"})
            .status_code
            == 403
        )
        approved = api[0].post(
            path + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-fixture-program",
            },
        )
        assert approved.status_code == 200 and approved.json()["status"] == "QUEUED", approved.text
        assert approved.json()["approval_status"] == "consumed"
        from cutback.server.policy_program_worker import advance_one

        ordinary = advance_one(
            api[2].state.sessions,
            api[2].state.artifact_store,
            execution_enabled=True,
            only_program_id=row["id"],
            synthetic_transport=False,
        )
        assert ordinary["status"] == "SYNTHETIC_EXECUTOR_REQUIRED"
        still_approved = api[0].get(path, headers=api[1]).json()
        assert still_approved["status"] == "QUEUED" and still_approved["requests"] == 0
        assert still_approved["runs"] == []
        cancelled = api[0].post(path + "/cancel", headers=api[1], json={})
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
        assert calls == []
    finally:
        executor.close()


def test_missing_complete_economics_abstains_and_cannot_consume_an_execution_budget(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, _, executor, calls, _, row, _ = prepared_program(
        api, tenant, tmp_path, monkeypatch, conservative_savings_per_success_usd=None
    )
    try:
        assert row["status"] == "ABSTAINED" and row["economic_envelope"]["eligible"] is False
        response = api[0].post(
            base + "/source-policy-programs/" + row["id"] + "/approve",
            headers=api[1],
            json={"program_hash": row["program_hash"], "approval_ref": "owned-fixture-program"},
        )
        assert response.status_code == 409 and calls == []
    finally:
        executor.close()
