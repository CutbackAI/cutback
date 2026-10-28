from uuid import UUID, uuid4

import pytest
from support_outcome_watch import prepare_watch

from cutback.server.outcome_watches import run_outcome_watch_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_busy_project_with_many_due_policies_does_not_hide_another_project(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.dataset_lineage import lock_lineage

    base, env, body, _, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    for _ in range(20):
        another = (
            api[0]
            .post(
                base + "/outcome-watches",
                headers=api[1],
                params={"environment_id": env},
                json={**body, "watch_id": str(uuid4())},
            )
            .json()
        )
        assert (
            api[0]
            .post(
                base + f"/outcome-watches/{another['id']}/approve",
                headers=api[1],
                json={
                    "approval_hash": another["approval_hash"],
                    "approval_ref": "owned-concurrent-watch",
                },
            )
            .status_code
            == 200
        )
    project = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-watch-fairness-" + uuid4().hex})
        .json()["id"]
    )
    writer = (
        api[0]
        .post(f"/v1/projects/{project}/keys", headers=api[1], json={"role": "ingest"})
        .json()["token"]
    )
    viewer = (
        api[0]
        .post(f"/v1/projects/{project}/keys", headers=api[1], json={"role": "viewer"})
        .json()["token"]
    )
    other_tenant = (
        project,
        {"Authorization": "Bearer " + writer},
        {"Authorization": "Bearer " + viewer},
    )
    other_path = tmp_path / "other-owned-project"
    other_path.mkdir()
    _, _, _, other, other_at = prepare_watch(api, other_tenant, other_path, monkeypatch)
    sessions = api[2].state.sessions
    with sessions.begin() as held:
        lock_lineage(held, UUID(tenant[0]))
        result = run_outcome_watch_one(sessions, enabled=True, now=max(at, other_at))
    assert result["status"] == "COMPLETED" and result["watch_id"] == other["id"], result
