"""Current credentials and fixed diagnostics, without pretending failures yielded charts."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from integration.test_analytics_dashboards import create_chart, read_chart
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from cutback.server.analytics_api import quota_bucket
from cutback.server.db import RateBucket

pytestmark = pytest.mark.postgres


def test_live_scoped_key_revocation_applies_to_every_dashboard_read(api, tenant):
    chart, _ = create_chart(api, tenant)
    root = f"/v1/projects/{tenant[0]}"
    key = api[0].post(root + "/keys", headers=api[1], json={"role": "viewer"}).json()
    headers = {"Authorization": "Bearer " + key["token"]}
    assert read_chart(api, tenant, chart, headers=headers).status_code == 200
    assert api[0].delete(root + "/keys/" + key["key_id"], headers=api[1]).status_code == 200
    assert read_chart(api, tenant, chart, headers=headers).status_code == 401
    assert api[0].get(root + "/analytics-dashboards", headers=headers).status_code == 401
    assert (
        api[0].get(root + "/analytics-dashboards/" + chart["id"], headers=headers).status_code
        == 401
    )


@pytest.mark.parametrize("failure,status", [(ValueError, 422), (SQLAlchemyError, 503)])
def test_read_failure_exposes_no_sql_values_or_partial_chart_data(
    api, tenant, monkeypatch, failure, status
):
    chart, _ = create_chart(api, tenant)

    def fail(*args, **kwargs):
        raise failure("owned-private-query-sentinel")

    monkeypatch.setattr("cutback.server.analytics_api.PostgresEventRepository.query_usage", fail)
    result = read_chart(api, tenant, chart)
    assert result.status_code == status
    assert "owned-private-query-sentinel" not in result.text
    assert "panels" not in result.json()


def test_inflight_read_and_project_erasure_use_compatible_ownership_locks(api, tenant, monkeypatch):
    from cutback.server.analytics_api import PostgresEventRepository

    chart, _ = create_chart(api, tenant)
    assert read_chart(api, tenant, chart).status_code == 200
    entered, release = threading.Event(), threading.Event()
    original = PostgresEventRepository.query_usage

    def held_read(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10), "OWNED_ANALYTICS_READ_BARRIER_EXPIRED"
        return original(*args, **kwargs)

    monkeypatch.setattr(PostgresEventRepository, "query_usage", held_read)
    root = f"/v1/projects/{tenant[0]}"
    project = api[0].get(root, headers=api[1]).json()
    with ThreadPoolExecutor(2) as pool:
        reading = pool.submit(read_chart, api, tenant, chart)
        assert entered.wait(5), "OWNED_ANALYTICS_READ_DID_NOT_START"
        deleting = pool.submit(
            api[0].delete, root, headers=api[1], params={"confirm_slug": project["slug"]}
        )
        try:
            waiting = False
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with api[2].state.engine.connect() as connection:
                    waiting = bool(
                        connection.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity "
                                "WHERE datname=current_database() "
                                "AND wait_event_type='Lock' AND query ILIKE '%FOR UPDATE%'"
                            )
                        )
                    )
                if waiting:
                    break
                time.sleep(0.02)
            assert waiting and not deleting.done(), "PROJECT_ERASURE_DID_NOT_WAIT_FOR_OWNED_READ"
        finally:
            release.set()
        assert reading.result(timeout=10).status_code == 200
        assert deleting.result(timeout=10).status_code == 200
    bucket = quota_bucket(UUID(tenant[0]))
    with api[2].state.sessions() as session:
        assert session.get(RateBucket, bucket) is None
    assert read_chart(api, tenant, chart, headers=api[1]).status_code == 404
    with api[2].state.sessions() as session:
        assert session.get(RateBucket, bucket) is None
