from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from support_outcome_watch import prepare_watch

from cutback.server.economics_db import ImprovementObservation, TaskCostSnapshot
from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow
from cutback.server.outcome_watches import run_outcome_watch_one
from cutback.server.traces import fingerprint

pytestmark = pytest.mark.postgres


def test_approved_closed_window_observes_regression_with_scoped_costs_and_no_duplicate_tick(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    assert run_outcome_watch_one(sessions)["status"] == "OUTCOME_WATCH_EXECUTION_DISABLED"
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(
            pool.map(
                lambda _: run_outcome_watch_one(
                    sessions, enabled=True, now=at, only_watch_id=row["id"]
                ),
                range(3),
            )
        )
    assert sum(result["status"] == "COMPLETED" for result in results) == 1, results
    assert all(result["provider_calls"] == 0 for result in results)
    assert (
        next(r for r in results if r["status"] == "COMPLETED")["next_action"] == "REVIEW_ROLLBACK"
    )
    path = base + f"/outcome-watches/{row['id']}/windows"
    response = api[0].get(path, headers=tenant[2])
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["windows_processed"] == page["retained_windows"] == len(page["items"]) == 1
    evidence = page["items"][0]["evidence"]
    assert fingerprint(evidence) == page["items"][0]["evidence_sha256"]
    assert evidence["report"]["candidate_declared_success_rate"] == "0.5"
    assert Decimal(evidence["report"]["declared_optimization_total_usd"]) == Decimal("0.0002")
    assert (
        evidence["report"]["production_savings_usd"] is None and not evidence["action_authorized"]
    )
    with sessions() as session:
        candidate = session.get(TaskCostSnapshot, UUID(evidence["candidate_snapshot_id"]))
        assert candidate.report["summary"]["submitted_tasks"] == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutcomeWatchWindow)
                .where(OutcomeWatchWindow.watch_record_id == UUID(row["id"]))
            )
            == 1
        )
        assert session.get(OutcomeWatch, UUID(row["id"])).windows_processed == 1
        assert session.get(ImprovementObservation, UUID(evidence["improvement_id"])) is not None


def test_missing_later_tasks_stay_unknown_and_missed_intervals_do_not_catch_up(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch, interval_seconds=3600)
    sessions = api[2].state.sessions
    result = run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    assert (
        result["status"] == "MISSING_EVIDENCE"
        and result["next_action"] == "COLLECT_MISSING_EVIDENCE"
    )
    page = api[0].get(base + f"/outcome-watches/{row['id']}/windows", headers=tenant[2]).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["evidence"]["skipped_previous_windows"] == 2
    assert page["items"][0]["evidence"]["report"] is None
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "IDLE"
    )


def test_history_pages_are_newest_first_with_exact_wire_hashes(api, tenant, tmp_path, monkeypatch):
    base, _, request, row, at = prepare_watch(
        api, tenant, tmp_path, monkeypatch, interval_seconds=3600
    )
    sessions = api[2].state.sessions
    earlier = datetime.fromisoformat(request["first_window_end"]) + timedelta(seconds=1)
    for cutoff in (earlier, at):
        result = run_outcome_watch_one(sessions, enabled=True, now=cutoff, only_watch_id=row["id"])
        assert result["status"] == "MISSING_EVIDENCE"
    path = base + f"/outcome-watches/{row['id']}/windows"
    newest = api[0].get(path, headers=tenant[2], params={"limit": 1}).json()
    assert newest["items"][0]["evidence"]["window_index"] == newest["next_after"] == 2
    older = api[0].get(path, headers=tenant[2], params={"limit": 1, "after": 2}).json()
    assert older["items"][0]["evidence"]["window_index"] == 0
    assert older["next_after"] is None
    assert newest["retained_windows"] == newest["windows_processed"] == 2
    for item in newest["items"] + older["items"]:
        assert fingerprint(item["evidence"]) == item["evidence_sha256"]
    state = api[0].get(base + f"/outcome-watches/{row['id']}", headers=tenant[2]).json()
    assert state["windows_skipped"] == 1
