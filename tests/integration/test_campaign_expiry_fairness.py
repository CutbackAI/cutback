"""Expiry must not block worker phases behind another project's mutation lock."""

import threading
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from integration.test_campaign_workflow import approve, prepare

from cutback.server.campaign_db import CampaignRecord
from cutback.server.campaign_retention import expire_campaigns
from cutback.server.dataset_lineage import lock_lineage

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_expiry_skips_busy_project_and_revisits_it_without_provider_execution(
    api, tenant, tmp_path, monkeypatch
):
    base, _, first, _ = prepare(api, tenant, tmp_path / "first", monkeypatch)
    approve(api, base, first)
    other = api[0].post(
        "/v1/projects", headers=api[1], json={"slug": "owned-expiry-peer-" + uuid4().hex}
    )
    assert other.status_code == 201
    project = other.json()["id"]
    key = api[0].post(f"/v1/projects/{project}/keys", headers=api[1], json={"role": "ingest"})
    assert key.status_code == 201
    second_base, _, second, _ = prepare(
        api,
        (project, {"Authorization": "Bearer " + key.json()["token"]}, tenant[2]),
        tmp_path / "second",
        monkeypatch,
    )
    approve(api, second_base, second)
    with api[2].state.sessions.begin() as session:
        for row in (first, second):
            session.get(CampaignRecord, UUID(row["id"])).deadline_at = datetime.now(
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
        assert expire_campaigns(api[2].state.sessions)["expired"] == 1
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    with api[2].state.sessions() as session:
        assert session.get(CampaignRecord, UUID(first["id"])).status == "QUEUED"
        peer = session.get(CampaignRecord, UUID(second["id"]))
        assert peer.status == "FAILED" and peer.requests_used == 0
    assert expire_campaigns(api[2].state.sessions, only_campaign_id=first["id"])["expired"] == 1
