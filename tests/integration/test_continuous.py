from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from support_evaluation import SyntheticExecutor, authorized_dataset

from cutback.server.continuous import queue_schedules
from cutback.server.enterprise_db import ExperimentSchedule
from cutback.server.worker import run_one


@pytest.mark.postgres
def test_schedule_exact_aggregate_budget_one_use_approval_and_no_catchup(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    path, store, dataset, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    now = datetime.now(UTC)
    spec = {
        "plan_id": plan["id"],
        "interval_seconds": 60,
        "max_runs": 2,
        "max_total_usd": "2",
        "max_total_requests": 1000,
        "first_run_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    assert (
        client.post(
            path + "/schedules", headers=admin, json={**spec, "max_total_usd": "0.01"}
        ).status_code
        == 422
    )
    response = client.post(path + "/schedules", headers=admin, json=spec)
    assert response.status_code == 201, response.text
    schedule = response.json()
    approval = {"approval_hash": schedule["approval_hash"], "approval_ref": "synthetic-schedule"}
    endpoint = path + "/schedules/" + schedule["id"] + "/approve"
    assert queue_schedules(app.state.sessions, enabled=True, now=now)["queued"] == 0
    assert client.post(endpoint, headers=tenant[2], json=approval).status_code == 403
    assert (
        client.post(
            endpoint, headers=admin, json={**approval, "approval_hash": "0" * 64}
        ).status_code
        == 409
    )
    assert client.post(endpoint, headers=admin, json=approval).status_code == 200
    assert client.post(endpoint, headers=admin, json=approval).status_code == 409
    assert queue_schedules(app.state.sessions, now=now)["queued"] == 0
    assert queue_schedules(app.state.sessions, enabled=True, now=now)["queued"] == 1
    assert queue_schedules(app.state.sessions, enabled=True, now=now)["queued"] == 0
    assert (
        queue_schedules(app.state.sessions, enabled=True, now=now + timedelta(seconds=61))["queued"]
        == 1
    )
    assert (
        queue_schedules(app.state.sessions, enabled=True, now=now + timedelta(hours=2))["queued"]
        == 0
    )
    with app.state.sessions() as session:
        row = session.get(ExperimentSchedule, UUID(schedule["id"]))
        assert row.reserved_usd == Decimal(2) and row.runs_queued == 2 and not row.enabled
    executor = SyntheticExecutor()
    assert run_one(app.state.sessions, store, executor=executor)["provider_calls"] == 0
    assert not executor.calls
    for queued in client.get(path + "/experiments", headers=admin).json():
        assert (
            client.post(
                path + f"/experiments/{queued['id']}/cancel", headers=admin, json={}
            ).status_code
            == 200
        )


@pytest.mark.postgres
def test_rollout_records_operator_actions_and_keeps_synthetic_evidence_unverified(
    api, tenant, tmp_path, monkeypatch
):
    client, admin, app = api
    path, store, dataset, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    assert (
        client.post(
            path + f"/experiments/plans/{plan['id']}/approve",
            headers=admin,
            json={"plan_hash": plan["plan_hash"], "approval_ref": "synthetic"},
        ).status_code
        == 200
    )
    client.post(path + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert (
        run_one(app.state.sessions, store, executor=SyntheticExecutor(), execution_enabled=True)[
            "status"
        ]
        == "COMPLETED"
    )
    rec = client.get(path + "/recommendations", headers=admin).json()[0]
    client.post(
        path + f"/recommendations/{rec['id']}/review",
        headers=admin,
        json={"decision": "accepted_for_staging", "reason": "synthetic-reviewed"},
    )
    proposal = client.post(
        path + "/rollouts",
        headers=admin,
        json={
            "recommendation_id": rec["id"],
            "environment": "staging",
            "baseline_ref": "base-sha",
            "rollback_ref": "rollback-sha",
            "guardrails": {"minimum_business_quality": 0.9, "min_observations": 10},
        },
    )
    assert proposal.status_code == 201, proposal.text
    rollout = proposal.json()
    root = path + "/rollouts/" + rollout["id"]
    transition = {"state": "DEPLOYED_CANARY", "operator_ref": "synthetic-stage-deploy"}
    assert client.post(root + "/transition", headers=admin, json=transition).status_code == 409
    assert (
        client.post(
            root + "/approve",
            headers=admin,
            json={"approval_hash": rollout["approval_hash"], "approval_ref": "synthetic-approval"},
        ).status_code
        == 200
    )
    assert not client.post(root + "/transition", headers=admin, json=transition).json()[
        "traffic_changed_by_cutback"
    ]
    window = {
        "observations": 20,
        "error_rate": 0,
        "schema_failure_rate": 0,
        "latency_p95_ms": 100,
        "cost_per_request_usd": 0.001,
        "business_quality": 1,
    }
    body = {
        "start": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
        "baseline": window,
        "candidate": window,
        "evidence_ref": "synthetic-window",
        "synthetic": True,
        "controlled": True,
        "independent_business_metric": True,
    }
    response = client.post(root + "/observations", headers=admin, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["evidence_tier"] == 1 and response.json()["status"] != "VALIDATED"
    body["candidate"] = {**window, "error_rate": 0.5}
    assert (
        client.post(root + "/observations", headers=admin, json=body).json()["status"]
        == "ROLLBACK_RECOMMENDED"
    )
    assert (
        client.post(
            root + "/transition",
            headers=admin,
            json={"state": "ROLLED_BACK", "operator_ref": "synthetic-rollback"},
        ).json()["status"]
        == "ROLLED_BACK"
    )
