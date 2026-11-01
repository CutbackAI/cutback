"""One frozen approval, synthetic independent source, real PG and no inference in HTTP."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integration.test_task_outcomes import declaration, environment, ingest, spans
from sqlalchemy import func, select
from support_routing_application import application

from cutback.agent import analyze, prepare
from cutback.agent_control import Control
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.execution_db import ExecutionCall
from cutback.server.routing_program_db import RoutingProgramRecord

pytestmark = pytest.mark.postgres


def prepared_program(api, tenant, tmp_path, monkeypatch, *, store=None, **changes):
    root = application(tmp_path)
    workflow = analyze(root)["routing_producers"][0]["workflow_id"]
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = store or EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    state = prepare(
        root,
        control,
        tenant[0],
        files=["tickets.jsonl"],
        rights_basis="synthetic",
        rights_ref="owned-routing-program",
        max_usd="100",
        max_requests=2000,
        workflow=workflow,
        minimum=1,
        independent_labels=True,
        representative=False,
        learned_routing=True,
        fixture_transport=True,
        min_test_samples=3,
    )
    task = declaration(
        workflow=workflow,
        components=[{"key": "owned-compute", "kind": "compute", "amount_usd": "1"}],
    )
    spans(api, tenant, task)
    assert ingest(api, tenant, task).status_code == 200
    base = f"/v1/projects/{tenant[0]}"
    snapshot = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "workflow": workflow,
            "expected_tasks": 1,
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    request = {
        "request_id": str(uuid4()),
        "template_plan_id": state["plan"]["id"],
        "baseline_snapshot_id": snapshot.json()["id"],
        "budget": {"max_usd": "100", "max_requests": 2000, "max_wall_seconds": 300},
        "conservative_savings_per_success_usd": "0.01",
        "forecast_successful_tasks": 100000,
        **changes,
    }
    response = api[0].post(base + "/routing/programs", headers=api[1], json=request)
    assert response.status_code == 201, response.text
    return base, root, store, response.json(), request


def test_prepare_approve_cancel_one_parent_and_metadata_has_no_instruction(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, row, request = prepared_program(api, tenant, tmp_path, monkeypatch)
    assert row["status"] == "READY_FOR_APPROVAL", row["economic_envelope"]
    assert row["economic_envelope"]["eligible"] is True
    assert row["economic_envelope"]["confirmation_sample_reduction"] is False
    assert row["specification"]["template"]["baseline"]["instruction"] == ""
    repeat = api[0].post(base + "/routing/programs", headers=api[1], json=request)
    assert repeat.status_code == 201 and repeat.json()["id"] == row["id"], repeat.text
    changed = api[0].post(
        base + "/routing/programs",
        headers=api[1],
        json={
            **request,
            "forecast_successful_tasks": 200000,
        },
    )
    assert changed.status_code == 409 and "INTENT_CHANGED" in changed.text
    path = base + "/routing/programs/" + row["id"]
    metadata = api[0].get(path, headers=tenant[2])
    assert metadata.status_code == 200 and metadata.json()["runs"] == []
    assert (
        api[0]
        .post(path + "/content", headers=tenant[2], json={"purpose": "owned-test"})
        .status_code
        == 403
    )
    content = api[0].post(path + "/content", headers=api[1], json={"purpose": "owned-test"})
    assert content.status_code == 200, content.text
    assert content.json()["specification"]["template"]["baseline"]["instruction"]
    approval = {"program_hash": row["program_hash"], "approval_ref": "owned-synthetic-only"}
    assert api[0].post(path + "/approve", headers=tenant[2], json=approval).status_code == 403
    approved = api[0].post(path + "/approve", headers=api[1], json=approval)
    assert approved.status_code == 200 and approved.json()["status"] == "QUEUED", approved.text
    assert api[0].post(path + "/approve", headers=api[1], json=approval).status_code == 409
    cancelled = api[0].post(path + "/cancel", headers=api[1], json={})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    assert api[0].post(path + "/cancel", headers=api[1], json={}).status_code == 200
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ExecutionCall)
                .where(ExecutionCall.project_id == UUID(tenant[0]))
            )
            == 0
        )
        assert session.get(RoutingProgramRecord, UUID(row["id"])).requests_used == 0
    assert "owned-compute" not in json.dumps(metadata.json())


def test_unknown_economics_abstains_and_never_gains_approval(api, tenant, tmp_path, monkeypatch):
    base, _, _, row, request = prepared_program(
        api, tenant, tmp_path, monkeypatch, forecast_successful_tasks=None
    )
    assert row["status"] == "ABSTAINED" and row["economic_envelope"]["eligible"] is False
    response = api[0].post(base + "/routing/programs", headers=api[1], json=request)
    assert response.status_code == 201 and response.json()["id"] == row["id"], response.text
    path = base + "/routing/programs/" + row["id"]
    assert (
        api[0]
        .post(
            path + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-test",
            },
        )
        .status_code
        == 409
    )
    assert row["economic_envelope"]["full_task_cost_advantage"] is None
