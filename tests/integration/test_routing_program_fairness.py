"""A busy project's whole-program transition cannot block another tenant."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integration.test_routing_program_api import prepared_program

from cutback.server.dataset_lineage import lock_lineage
from cutback.server.routing_program_db import RoutingProgramRecord
from cutback.server.routing_program_retention import expire_programs
from cutback.server.routing_program_worker import advance_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


@pytest.mark.parametrize("expiry", [False, True])
def test_dispatcher_skips_busy_project_and_preserves_both_scopes(
    api, tenant, tmp_path, monkeypatch, expiry
):
    base, _, store, first, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    other = api[0].post(
        "/v1/projects", headers=api[1], json={"slug": "owned-program-peer-" + uuid4().hex}
    )
    assert other.status_code == 201
    project = other.json()["id"]
    key = api[0].post(f"/v1/projects/{project}/keys", headers=api[1], json={"role": "ingest"})
    assert key.status_code == 201
    second_base, _, _, second, _ = prepared_program(
        api,
        (project, {"Authorization": "Bearer " + key.json()["token"]}, tenant[2]),
        tmp_path / "peer",
        monkeypatch,
        store=store,
    )
    for path, row in ((base, first), (second_base, second)):
        response = api[0].post(
            path + "/routing/programs/" + row["id"] + "/approve",
            headers=api[1],
            json={
                "program_hash": row["program_hash"],
                "approval_ref": "owned-dispatch-test",
            },
        )
        assert response.status_code == 200
    if expiry:
        with api[2].state.sessions.begin() as session:
            for row in (first, second):
                session.get(RoutingProgramRecord, UUID(row["id"])).deadline_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
    acquired, release = threading.Event(), threading.Event()

    def hold():
        with api[2].state.sessions.begin() as session:
            lock_lineage(session, UUID(tenant[0]))
            acquired.set()
            assert release.wait(15)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert acquired.wait(5)
        if expiry:
            assert expire_programs(api[2].state.sessions)["expired"] == 1
        else:
            result = advance_one(
                api[2].state.sessions, store, execution_enabled=True, synthetic_transport=True
            )
            assert result["status"] == "WAITING_STATIC" and result["program_id"] == second["id"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    with api[2].state.sessions() as session:
        assert session.get(RoutingProgramRecord, UUID(first["id"])).status == "QUEUED"
        assert session.get(RoutingProgramRecord, UUID(second["id"])).status == (
            "EXPIRED" if expiry else "WAITING_STATIC"
        )
        assert session.get(RoutingProgramRecord, UUID(second["id"])).requests_used == 0
    if expiry:
        assert expire_programs(api[2].state.sessions, only_program_id=first["id"])["expired"] == 1
