"""Synthetic control-plane fixtures, not an application source/deployment claim."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from test_execution_ledger import ledger_for
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.evals.experiments import Execution
from cutback.server.db import ChangeProposal, ExperimentRunRecord, JobRecord, RecommendationRecord


def add_tasks(api, tenant, release, end, outcomes, *, service="synthetic-shop", cost="2"):
    for index, outcome in enumerate(outcomes):
        occurred = end - timedelta(seconds=30 - index)
        item = declaration(
            release=release,
            occurred_at=occurred.isoformat(),
            outcome=outcome,
            service=service,
            components=[{"key": "synthetic-compute", "kind": "compute", "amount_usd": cost}],
        )
        spans(
            api,
            tenant,
            item,
            ended_ns=int((occurred + timedelta(seconds=1)).timestamp() * 1000000000),
        )
        assert ingest(api, tenant, item).status_code == 200


def prepare_watch(
    api, tenant, tmp_path, monkeypatch, *, outcomes=("success", "failure"), approve=True, **changes
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(
        identifier, Execution("owned-not-retained", Decimal("0.0002"), 1, 4, 3), "completed"
    )
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
        session.scalar(
            select(JobRecord).where(JobRecord.run_id == ledger.run_id)
        ).status = "CANCELLED"
        rec = RecommendationRecord(
            project_id=UUID(tenant[0]),
            run_id=ledger.run_id,
            status="synthetic_fixture",
            evidence={"fixture": "metadata_reference_not_application_approval"},
        )
        session.add(rec)
        session.flush()
        change = ChangeProposal(
            project_id=UUID(tenant[0]),
            recommendation_id=rec.id,
            evidence={"diff_sha256": "a" * 64, "fixture": "synthetic_metadata_reference_only"},
        )
        session.add(change)
        session.flush()
        change_id = str(change.id)
    now = datetime.now(UTC).replace(microsecond=0)
    baseline_end = now - timedelta(hours=3)
    candidate_end = now - timedelta(hours=2)
    add_tasks(api, tenant, "owned-reference", baseline_end, ("success", "success"))
    add_tasks(api, tenant, "owned-candidate", candidate_end, outcomes, cost="0.25")
    # A different service under the same workflow/release must never contaminate the window.
    add_tasks(
        api,
        tenant,
        "owned-candidate",
        candidate_end,
        ("failure",),
        service="other-owned-service",
        cost="100",
    )
    base = f"/v1/projects/{tenant[0]}"
    env = environment(api, tenant[0])
    snapshot = api[0].post(
        base + "/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": env},
        json={
            "snapshot_id": str(uuid4()),
            "start": (baseline_end - timedelta(minutes=1)).isoformat(),
            "end": baseline_end.isoformat(),
            "workflow": "synthetic.task",
            "service": "synthetic-shop",
            "release": "owned-reference",
            "expected_tasks": 2,
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    request = {
        "watch_id": str(uuid4()),
        "key": "owned-change-followup",
        "baseline_snapshot_id": snapshot.json()["id"],
        "baseline_sha256": snapshot.json()["evidence_sha256"],
        "change_id": change_id,
        "source_diff_sha256": "a" * 64,
        "optimization_run_ids": [str(ledger.run_id)],
        "release_linkage_attested": True,
        "candidate_release": "owned-candidate",
        "window_seconds": 60,
        "interval_seconds": 86400,
        "first_window_end": candidate_end.isoformat(),
        "grace_seconds": 0,
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "max_windows": 4,
        "expected_tasks": 2,
        "assumptions": {
            "min_tasks_per_window": 2,
            "forecast_successful_tasks": 100,
            "all_optimization_runs_declared_complete": True,
            "other_optimization_costs_declared_complete": True,
        },
        **changes,
    }
    response = api[0].post(
        base + "/outcome-watches", params={"environment_id": env}, headers=api[1], json=request
    )
    assert response.status_code == 201, response.text
    row = response.json()
    if approve:
        response = api[0].post(
            base + f"/outcome-watches/{row['id']}/approve",
            headers=api[1],
            json={"approval_hash": row["approval_hash"], "approval_ref": "owned-metadata-policy"},
        )
        assert response.status_code == 200, response.text
        row = response.json()
    return base, env, request, row, datetime.now(UTC)
