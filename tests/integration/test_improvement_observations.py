from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from test_task_outcomes import declaration, environment, ingest, spans

from cutback.server.db import ExperimentRunRecord, JobRecord
from cutback.server.economics_db import ImprovementObservation, TaskCostSnapshot

pytestmark = pytest.mark.postgres


def freeze(api, tenant, release, outcomes=("success", "success"), cost="2"):
    for outcome in outcomes:
        item = declaration(
            release=release,
            outcome=outcome,
            components=[{"key": "synthetic-compute", "kind": "compute", "amount_usd": cost}],
        )
        spans(api, tenant, item)
        assert ingest(api, tenant, item).status_code == 200
    response = api[0].post(
        f"/v1/projects/{tenant[0]}/task-cost-snapshots",
        headers=api[1],
        params={"environment_id": environment(api, tenant[0])},
        json={
            "snapshot_id": str(uuid4()),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "release": release,
            "expected_tasks": len(outcomes),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def body(before, after, **changes):
    return {
        "observation_id": str(uuid4()),
        "baseline_snapshot_id": before,
        "candidate_snapshot_id": after,
        "assumptions": {
            "all_optimization_runs_declared_complete": True,
            "other_optimization_costs_declared_complete": True,
            "forecast_successful_tasks": 100,
            "min_tasks_per_window": 2,
        },
        **changes,
    }


def test_canonical_search_cost_not_duplicated_and_cheap_worse_rejected(
    api, tenant, tmp_path, monkeypatch
):
    from test_execution_ledger import ledger_for

    from cutback.evals.experiments import Execution

    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    for amount in ("0.0002", "0.0003"):
        call = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
        ledger.record(
            call, Execution("synthetic-not-retained", Decimal(amount), 2, 4, 3), "completed"
        )
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
        session.get(JobRecord, ledger.job_id).status = "CANCELLED"
    before = freeze(api, tenant, "synthetic-reference")
    after = freeze(api, tenant, "synthetic-cheap-but-worse", ("success", "failure"), "0.25")
    path = f"/v1/projects/{tenant[0]}/improvement-observations"
    request = body(before, after, optimization_run_ids=[str(ledger.run_id)])
    assert api[0].post(path, headers=tenant[2], json=request).status_code == 403
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(lambda _: api[0].post(path, headers=api[1], json=request), range(3))
        )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    result = responses[0].json()["evidence"]
    assert len(result["run_costs"]) == 1
    assert Decimal(result["report"]["declared_optimization_total_usd"]) == Decimal("0.0005")
    assert result["report"]["descriptive_outcome_non_regression"] is False
    assert "declared_outcome_regression" in result["report"]["reasons"]
    assert result["release_linkage"] == "unlinked_window_comparison"
    assert result["report"]["production_savings_usd"] is None
    assert not result["report"]["deployment_authorized"]
    assert (
        api[0].post(path, headers=api[1], json={**request, "optimization_run_ids": []}).status_code
        == 409
    )
    listing = api[0].get(path, headers=tenant[2])
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    scoped = api[0].get(
        path, headers=tenant[2], params={"environment_id": environment(api, tenant[0])}
    )
    assert scoped.status_code == 200 and len(scoped.json()["items"]) == 1
    different = api[0].get(path, headers=tenant[2], params={"environment_id": str(uuid4())})
    assert different.status_code == 200 and different.json()["items"] == []


def test_unknown_overhead_forecasts_scope_and_snapshot_integrity(api, tenant):
    before = freeze(api, tenant, "synthetic-reference")
    after = freeze(api, tenant, "synthetic-candidate", cost="1")
    path = f"/v1/projects/{tenant[0]}/improvement-observations"
    missing = body(before, after, assumptions={})
    response = api[0].post(path, headers=api[1], json=missing)
    assert response.status_code == 200, response.text
    result = response.json()["evidence"]["report"]
    assert result["declared_optimization_total_usd"] is None
    assert "future_successful_task_volume_unknown" in result["reasons"]
    assert "optimization_overhead_incomplete" in result["reasons"]
    assert (
        api[0]
        .post(path, headers=api[1], json=body(before, after, optimization_run_ids=[str(uuid4())]))
        .status_code
        == 404
    )
    assert api[0].post(path, headers=api[1], json=body(before, before)).status_code == 422
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "synthetic-" + uuid4().hex})
        .json()["id"]
    )
    assert (
        api[0]
        .post(
            f"/v1/projects/{other}/improvement-observations",
            headers=api[1],
            json=body(before, after),
        )
        .status_code
        == 404
    )
    assert (
        api[0].get(f"/v1/projects/{other}/improvement-observations", headers=tenant[2]).status_code
        == 403
    )
    with api[2].state.sessions.begin() as session:
        row = session.get(TaskCostSnapshot, UUID(after))
        row.report = {**row.report, "evidence_scope": "tampered"}
    assert api[0].post(path, headers=api[1], json=body(before, after)).status_code == 409


def test_observation_retention_keeps_referenced_snapshot_then_prunes_and_exports(api, tenant):
    from cutback.server.operations import maintain

    before = freeze(api, tenant, "synthetic-reference")
    after = freeze(api, tenant, "synthetic-candidate", cost="1")
    path = f"/v1/projects/{tenant[0]}/improvement-observations"
    response = api[0].post(path, headers=api[1], json=body(before, after))
    assert response.status_code == 200, response.text
    old = datetime.now(UTC) - timedelta(days=40)
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(TaskCostSnapshot)
            .where(TaskCostSnapshot.project_id == UUID(tenant[0]))
            .values(created_at=old)
        )
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    assert (
        api[0]
        .get(f"/v1/projects/{tenant[0]}/task-cost-snapshots/{before}", headers=tenant[2])
        .status_code
        == 200
    )
    export = api[0].get(f"/v1/projects/{tenant[0]}/export", headers=api[1])
    assert '"table": "improvement_observations"' in export.text
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(ImprovementObservation)
            .where(ImprovementObservation.project_id == UUID(tenant[0]))
            .values(created_at=old)
        )
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    assert api[0].get(path, headers=tenant[2]).json()["items"] == []
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TaskCostSnapshot)
                .where(TaskCostSnapshot.project_id == UUID(tenant[0]))
            )
            == 0
        )


def test_search_effort_endpoint_never_authorizes_spend(api, tenant):
    path = f"/v1/projects/{tenant[0]}/search-effort"
    response = api[0].post(path, headers=api[1], json={"owner_spend_cap_usd": "10"})
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "abstain" and response.json()["provider_calls"] == 0
    assert not response.json()["paid_calls_authorized"]
    assert (
        api[0].post(path, headers=tenant[2], json={"owner_spend_cap_usd": "10"}).status_code == 403
    )


def test_uncertain_search_cost_stays_unknown_and_late_settlement_requires_new_receipt(
    api, tenant, tmp_path, monkeypatch
):
    from test_execution_ledger import ledger_for

    from cutback.evals.experiments import Execution

    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    call = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(call, None, "uncertain")
    with ledger.sessions.begin() as session:
        session.get(ExperimentRunRecord, ledger.run_id).status = "CANCELLED"
        session.get(JobRecord, ledger.job_id).status = "CANCELLED"
    before = freeze(api, tenant, "synthetic-reference")
    after = freeze(api, tenant, "synthetic-candidate", cost="1")
    path = f"/v1/projects/{tenant[0]}/improvement-observations"
    request = body(before, after, optimization_run_ids=[str(ledger.run_id)])
    response = api[0].post(path, headers=api[1], json=request)
    assert response.status_code == 200, response.text
    original = response.json()
    report = original["evidence"]["report"]
    assert report["declared_optimization_total_usd"] is None
    assert report["known_optimization_run_subtotal_usd"] is None
    ledger.record(
        call, Execution("synthetic-not-retained", Decimal("0.0002"), 2, 4, 3), "completed"
    )
    assert api[0].post(path, headers=api[1], json=request).json() == original
    updated = api[0].post(path, headers=api[1], json={**request, "observation_id": str(uuid4())})
    assert updated.status_code == 200, updated.text
    assert Decimal(
        updated.json()["evidence"]["report"]["declared_optimization_total_usd"]
    ) == Decimal("0.0002")
    assert updated.json()["evidence"]["report"]["decision"] == "prepare_independent_confirmation"
