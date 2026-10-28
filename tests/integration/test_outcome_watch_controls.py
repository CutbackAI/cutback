from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from support_outcome_watch import prepare_watch

from cutback.server.db import AccessKey, Project
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.operations import maintain
from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow
from cutback.server.outcome_watches import run_outcome_watch_one

pytestmark = pytest.mark.postgres


def test_watch_hash_approval_rbac_pause_resume_and_replay_cannot_reset_counters(
    api, tenant, tmp_path, monkeypatch
):
    base, env, body, row, at = prepare_watch(
        api, tenant, tmp_path, monkeypatch, approve=False, max_windows=1
    )
    path = base + f"/outcome-watches/{row['id']}"
    sessions = api[2].state.sessions
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "IDLE"
    )
    approval = {"approval_hash": row["approval_hash"], "approval_ref": "owned-policy-review"}
    assert api[0].post(path + "/approve", headers=tenant[2], json=approval).status_code == 403
    assert (
        api[0]
        .post(path + "/approve", headers=api[1], json={**approval, "approval_hash": "b" * 64})
        .status_code
        == 409
    )
    assert api[0].get(path, headers=tenant[1]).status_code == 403
    assert api[0].post(path + "/approve", headers=api[1], json=approval).status_code == 200
    state = {"approval_hash": row["approval_hash"], "enabled": False}
    assert api[0].post(path + "/state", headers=api[1], json=state).json()["status"] == "PAUSED"
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "IDLE"
    )
    assert (
        api[0].post(path + "/state", headers=api[1], json={**state, "enabled": True}).status_code
        == 200
    )
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "COMPLETED"
    )
    replay = api[0].post(
        base + "/outcome-watches", headers=api[1], params={"environment_id": env}, json=body
    )
    assert (
        replay.status_code == 201
        and replay.json()["windows_processed"] == 1
        and replay.json()["status"] == "EXHAUSTED"
    )
    assert api[0].post(path + "/approve", headers=api[1], json=approval).status_code == 409
    changed = {**body, "max_windows": 2}
    assert (
        api[0]
        .post(
            base + "/outcome-watches", headers=api[1], params={"environment_id": env}, json=changed
        )
        .status_code
        == 409
    )
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-watch-other-" + uuid4().hex})
        .json()["id"]
    )
    assert (
        api[0].get(f"/v1/projects/{other}/outcome-watches/{row['id']}", headers=api[1]).status_code
        == 404
    )
    assert (
        api[0]
        .get(f"/v1/projects/{other}/outcome-watches/{row['id']}", headers=tenant[2])
        .status_code
        == 403
    )


@pytest.mark.parametrize("damage", ["hash", "approver", "baseline", "expiry"])
def test_current_authority_hash_retained_reference_and_expiry_stop_new_windows(
    api, tenant, tmp_path, monkeypatch, damage
):
    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        watch = session.get(OutcomeWatch, UUID(row["id"]))
        if damage == "hash":
            policy = {**watch.specification["policy"], "max_windows": 5}
            watch.specification = {**watch.specification, "policy": policy}
        elif damage == "approver":
            key = AccessKey(
                project_id=UUID(tenant[0]),
                role="project_admin",
                digest=uuid4().hex * 2,
                revoked_at=datetime.now(UTC),
            )
            session.add(key)
            session.flush()
            watch.approved_by = str(key.id)
        elif damage == "baseline":
            baseline = session.get(
                TaskCostSnapshot, UUID(watch.specification["policy"]["baseline_snapshot_id"])
            )
            baseline.report = {**baseline.report, "evidence_scope": "owned-tamper"}
        else:
            at = datetime.fromisoformat(watch.specification["policy"]["expires_at"]) + timedelta(
                seconds=1
            )
    result = run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    assert result["status"] == ("EXPIRED" if damage == "expiry" else "BLOCKED"), result
    assert (
        api[0]
        .get(base + f"/outcome-watches/{row['id']}/windows", headers=tenant[2])
        .json()["windows_processed"]
        == 0
    )


def test_lost_nonbillable_window_commit_ack_reconciles_without_duplicate_work(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    begin = sessions.begin

    @contextmanager
    def lose_ack():
        with begin() as session:
            yield session
        raise ConnectionError("OWNED_METADATA_WINDOW_ACK_LOST")

    with monkeypatch.context() as scope:
        scope.setattr(sessions, "begin", lose_ack)
        with pytest.raises(ConnectionError, match="ACK_LOST"):
            run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "IDLE"
    )
    page = api[0].get(base + f"/outcome-watches/{row['id']}/windows", headers=tenant[2]).json()
    assert len(page["items"]) == page["windows_processed"] == 1


def test_watch_export_retention_preserves_counter_and_project_delete_cleans_scoped_data(
    api, tenant, tmp_path, monkeypatch
):
    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    exported = api[0].get(base + "/export", headers=api[1])
    assert (
        '"table": "outcome_watches"' in exported.text
        and '"table": "outcome_watch_windows"' in exported.text
    )
    with sessions.begin() as session:
        session.execute(
            update(OutcomeWatchWindow)
            .where(OutcomeWatchWindow.watch_record_id == UUID(row["id"]))
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )
    maintain(sessions, project_id=UUID(tenant[0]), apply=True)
    page = api[0].get(base + f"/outcome-watches/{row['id']}/windows", headers=tenant[2]).json()
    assert page["windows_processed"] == 1 and page["retained_windows"] == 0 and page["items"] == []
    with sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(base, headers=api[1], params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    with sessions() as session:
        assert (
            session.scalar(
                select(OutcomeWatch.id).where(OutcomeWatch.project_id == UUID(tenant[0]))
            )
            is None
        )


def test_revocation_during_calculation_rolls_back_all_new_snapshots_and_comparisons(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server import outcome_watches

    base, _, _, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    key = api[0].post(base + "/keys", headers=api[1], json={"role": "project_admin"}).json()
    headers = {"Authorization": "Bearer " + key["token"]}
    approved = api[0].post(
        base + f"/outcome-watches/{row['id']}/approve",
        headers=headers,
        json={"approval_hash": row["approval_hash"], "approval_ref": "owned-revocable-observation"},
    )
    assert approved.status_code == 200
    original = outcome_watches.freeze_task_snapshot

    def revoke_after_snapshot(*args, **kwargs):
        candidate = original(*args, **kwargs)
        revoked = api[0].delete(base + f"/keys/{key['key_id']}", headers=api[1])
        assert revoked.status_code == 200, revoked.text
        return candidate

    sessions = api[2].state.sessions
    with monkeypatch.context() as scope:
        scope.setattr(outcome_watches, "freeze_task_snapshot", revoke_after_snapshot)
        with pytest.raises(ValueError, match="AUTHORITY_CHANGED_DURING_CALCULATION"):
            run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    with sessions() as session:
        snapshots = session.scalars(
            select(TaskCostSnapshot).where(TaskCostSnapshot.project_id == UUID(tenant[0]))
        ).all()
        assert len(snapshots) == 1
        assert session.get(OutcomeWatch, UUID(row["id"])).windows_processed == 0
    assert (
        run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])["status"]
        == "BLOCKED"
    )


def test_active_baseline_retention_pin_is_bounded_by_policy_expiry(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.task_economics import retain_task_outcomes

    _, _, request, row, _ = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    old = datetime.now(UTC) - timedelta(days=40)
    baseline_id = UUID(request["baseline_snapshot_id"])
    with sessions.begin() as session:
        session.get(TaskCostSnapshot, baseline_id).created_at = old
        retain_task_outcomes(
            session,
            datetime.now(UTC) - timedelta(days=30),
            lambda column: column == UUID(tenant[0]),
        )
    with sessions() as session:
        assert session.get(TaskCostSnapshot, baseline_id) is not None
    with sessions.begin() as session:
        policy = session.get(OutcomeWatch, UUID(row["id"]))
        # Simulate passage of policy time, not a new approval or altered evidence result.
        policy.specification = {
            **policy.specification,
            "policy": {**policy.specification["policy"], "expires_at": old.isoformat()},
        }
        retain_task_outcomes(
            session,
            datetime.now(UTC) - timedelta(days=30),
            lambda column: column == UUID(tenant[0]),
        )
    with sessions() as session:
        assert session.get(TaskCostSnapshot, baseline_id) is None
        policy = session.get(OutcomeWatch, UUID(row["id"]))
        assert policy.status == "EXPIRED" and not policy.enabled


def test_policy_project_quota_and_missing_canonical_cost_family_fail_closed(
    api, tenant, tmp_path, monkeypatch
):
    base, env, request, row, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        original = session.get(OutcomeWatch, UUID(row["id"]))
        for _ in range(99):
            session.add(
                OutcomeWatch(
                    project_id=original.project_id,
                    environment_id=original.environment_id,
                    watch_id=uuid4(),
                    approval_hash=original.approval_hash,
                    specification=original.specification,
                    next_run_at=original.next_run_at,
                )
            )
    response = api[0].post(
        base + "/outcome-watches",
        params={"environment_id": env},
        headers=api[1],
        json={**request, "watch_id": str(uuid4())},
    )
    assert response.status_code == 409 and "PROJECT_POLICY_QUOTA" in response.text
    from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord

    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, UUID(request["optimization_run_ids"][0]))
        plan = session.get(ExperimentPlanRecord, run.plan_id)
        # A retained plan marker without its family relation cannot prove complete costs.
        plan.specification = {**plan.specification, "campaign_id": str(uuid4())}
    result = run_outcome_watch_one(sessions, enabled=True, now=at, only_watch_id=row["id"])
    assert result["status"] == "BLOCKED" and "FAMILY" in result["code"]
