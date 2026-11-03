"""Actual PostgreSQL chart definitions, scoped canonical data and conservative retention."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from cutback.server.db import AuditEvent, DailyUsage, Environment, RateBucket, StoredEvent
from cutback.server.monitoring_db import SavedView
from cutback.server.operations import maintain

pytestmark = pytest.mark.postgres


def chart_body(**changes):
    return {
        "dashboard_id": str(uuid4()),
        "key": "owned-analytics-" + uuid4().hex,
        "expected_revision": 0,
        "environment": "staging",
        "window_days": 7,
        "panels": [
            {
                "key": "volume",
                "metric": "observed_attempts",
                "chart": "line",
                "group_by": "day",
                "traffic_kind": "application",
            },
            {
                "key": "cost",
                "metric": "known_modeled_cost_subtotal_usd",
                "group_by": "model",
                "traffic_kind": "application",
            },
            {
                "key": "latency",
                "metric": "latency_p95_ms",
                "chart": "stat",
                "traffic_kind": "application",
            },
        ],
        **changes,
    }


def create_chart(api, tenant, body=None):
    body = body or chart_body()
    result = api[0].post(
        f"/v1/projects/{tenant[0]}/analytics-dashboards", headers=api[1], json=body
    )
    assert result.status_code == 201, result.text
    return result.json(), body


def read_chart(api, tenant, chart, **changes):
    return api[0].get(
        f"/v1/projects/{tenant[0]}/analytics-dashboards/{chart['id']}/data",
        headers=changes.get("headers", tenant[2]),
        params={"expected_revision": changes.get("revision", chart["revision"])},
    )


def ingest(api, tenant, *events):
    result = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={
            "project_id": tenant[0],
            "events": [event.model_dump(mode="json") for event in events],
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_shared_definition_current_rights_audit_and_no_inferred_empty_health(api, tenant):
    path = f"/v1/projects/{tenant[0]}/analytics-dashboards"
    body = chart_body()
    for denied in (tenant[1], tenant[2]):
        assert api[0].post(path, headers=denied, json=body).status_code == 403
    created, _ = create_chart(api, tenant, body)
    assert create_chart(api, tenant, body)[0] == created
    listing = api[0].get(path, headers=tenant[2]).json()
    assert listing["items"][0]["id"] == created["id"]
    assert (
        not api[0].get(f"/v1/projects/{tenant[0]}/saved-views", headers=tenant[2]).json()["items"]
    )
    assert not api[0].get(f"/v1/projects/{tenant[0]}/dashboards", headers=tenant[2]).json()["items"]
    data = read_chart(api, tenant, created)
    assert data.status_code == 200, data.text
    report = data.json()
    assert not report["content_included"] and report["population_coverage"] == "unknown"
    assert report["panels"][0]["summary"]["observed_attempts"] == 0
    assert report["panels"][0]["summary"]["value"] is None
    assert len(report["panels"][0]["points"]) == 7
    assert all(point["value"] is None for point in report["panels"][0]["points"])
    assert read_chart(api, tenant, created, headers=tenant[1]).status_code == 403
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.project_id == UUID(tenant[0]),
                    AuditEvent.action == "analytics_dashboard_updated",
                )
            )
            == 1
        )


def test_scoped_data_preserves_unknown_money_and_excludes_other_environment_and_overhead(
    api, tenant, event
):
    entries = [
        event.model_copy(
            update={"event_id": uuid4(), "traffic_kind": "application", "workflow_id": "owned-task"}
        ),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "workflow_id": "owned-task",
                "requested_model": "owned-unpriced-model",
                "response_model": "owned-unpriced-model",
            }
        ),
        event.model_copy(
            update={"event_id": uuid4(), "traffic_kind": "evaluation", "workflow_id": "owned-task"}
        ),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "environment": "production",
                "traffic_kind": "application",
                "workflow_id": "owned-task",
            }
        ),
    ]
    ingest(api, tenant, *entries[:3])
    denied = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={"project_id": tenant[0], "events": [entries[3].model_dump(mode="json")]},
    )
    assert denied.status_code == 403
    with api[2].state.sessions.begin() as session:
        session.add(Environment(project_id=UUID(tenant[0]), name="production"))
    key = api[0].post(
        f"/v1/projects/{tenant[0]}/keys",
        headers=api[1],
        json={"role": "ingest", "environment": "production"},
    )
    assert key.status_code == 201
    accepted = api[0].post(
        "/v1/ingest/events",
        headers={"Authorization": "Bearer " + key.json()["token"]},
        json={"project_id": tenant[0], "events": [entries[3].model_dump(mode="json")]},
    )
    assert accepted.status_code == 200
    chart, _ = create_chart(api, tenant)
    result = read_chart(api, tenant, chart)
    assert result.status_code == 200, result.text
    panels = result.json()["panels"]
    assert panels[0]["summary"]["observed_attempts"] == 2
    cost = panels[1]["summary"]
    assert cost["modeled_cost_usd"] is None and cost["unpriced_attempts"] == 1
    assert Decimal(cost["known_modeled_cost_subtotal_usd"]) > 0
    assert panels[2]["summary"]["value"] == "125.0"
    assert "owned-unpriced-model" in {point["key"] for point in panels[1]["points"]}
    assert any(point["value"] is None for point in panels[1]["points"])
    raw = api[0].get(f"/v1/projects/{tenant[0]}/usage", headers=tenant[2]).json()
    assert raw["summary"]["observed_attempts"] == 4


def test_actual_retention_keeps_scoped_counts_but_does_not_rebuild_quantiles(api, tenant, event):
    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
    entries = [
        event.model_copy(
            update={"event_id": uuid4(), "traffic_kind": "application", "workflow_id": "owned-task"}
        )
        for _ in range(3)
    ]
    ingest(api, tenant, *entries)
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(tenant[0]))
            .values(occurred_at=old)
        )
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.event_id == entries[-1].event_id,
                StoredEvent.project_id == UUID(tenant[0]),
            )
            .values(occurred_at=old - timedelta(days=1))
        )
    body = chart_body(window_days=90)
    for panel in body["panels"]:
        panel["workflow"] = "owned-task"
    chart, _ = create_chart(api, tenant, body)
    before = read_chart(api, tenant, chart).json()
    assert before["panels"][2]["summary"]["value"] == "125.0"
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    after = read_chart(api, tenant, chart)
    assert after.status_code == 200, after.text
    panels = after.json()["panels"]
    assert panels[0]["summary"]["observed_attempts"] == 3
    assert panels[1]["summary"]["value"] == before["panels"][1]["summary"]["value"]
    assert panels[2]["summary"]["value"] is None
    assert panels[2]["archived_daily_rows"] > 0
    assert sum(point["value"] is not None for point in panels[0]["points"]) == 2
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(DailyUsage)
            .where(DailyUsage.project_id == UUID(tenant[0]))
            .values(day=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
        )
    partial = read_chart(api, tenant, chart).json()
    assert partial["panels"][0]["partial_days_excluded"]
    assert partial["panels"][1]["summary"]["modeled_cost_usd"] is None
    assert partial["panels"][2]["summary"]["value"] is None


def test_concurrent_edits_exact_lost_reply_reconciliation_and_stale_delete(api, tenant):
    chart, body = create_chart(api, tenant)
    path = f"/v1/projects/{tenant[0]}/analytics-dashboards"
    variants = [{**body, "expected_revision": 1, "window_days": days} for days in (30, 90)]
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda value: api[0].post(path, headers=api[1], json=value), variants)
        )
    assert sorted(result.status_code for result in results) == [201, 409]
    winner = next(
        value for value, result in zip(variants, results, strict=True) if result.status_code == 201
    )
    assert api[0].post(path, headers=api[1], json=winner).json()["revision"] == 2
    assert read_chart(api, tenant, chart).status_code == 409
    target = path + "/" + chart["id"]
    assert api[0].delete(target, params={"expected_revision": 1}, headers=api[1]).status_code == 409
    assert (
        api[0].delete(target, params={"expected_revision": 2}, headers=tenant[2]).status_code == 403
    )
    assert api[0].delete(target, params={"expected_revision": 2}, headers=api[1]).status_code == 200
    assert api[0].get(target, headers=tenant[2]).status_code == 404
    replay = api[0].post(path, headers=api[1], json=body)
    assert replay.status_code == 409 and replay.json()["code"] == "ANALYTICS_DASHBOARD_WAS_DELETED"


def test_tenant_identity_type_boundaries_and_untrusted_query_fields(api, tenant):
    chart, body = create_chart(api, tenant)
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
        .json()["id"]
    )
    root = f"/v1/projects/{tenant[0]}"
    assert (
        api[0]
        .get(f"/v1/projects/{other}/analytics-dashboards/{chart['id']}", headers=api[1])
        .status_code
        == 404
    )
    for invalid in (
        {**body, "key": "different", "expected_revision": 0},
        {**body, "dashboard_id": str(uuid4()), "expected_revision": 1},
    ):
        assert (
            api[0].post(root + "/analytics-dashboards", headers=api[1], json=invalid).status_code
            == 409
        )
    for invalid in (
        {**body, "raw_sql": "SYNTHETIC_SECRET_QUERY"},
        {**body, "window_days": True},
        {**body, "window_days": 2},
        {**body, "panels": body["panels"] * 3},
        {**body, "panels": [body["panels"][0]] * 2},
        {
            **body,
            "panels": [{"key": "bad", "metric": "errors", "chart": "line", "group_by": "model"}],
        },
    ):
        result = api[0].post(root + "/analytics-dashboards", headers=api[1], json=invalid)
        assert result.status_code == 422 and "SYNTHETIC_SECRET_QUERY" not in result.text
    collision = {
        "view_id": chart["id"],
        "key": chart["key"],
        "expected_revision": 1,
        "environment": "staging",
        "page": "traces",
    }
    assert api[0].post(root + "/saved-views", headers=api[1], json=collision).status_code == 409
    assert (
        api[0]
        .delete(
            root + "/saved-views/" + chart["id"], params={"expected_revision": 1}, headers=api[1]
        )
        .status_code
        == 404
    )


def test_pagination_and_shared_project_quota_do_not_depend_on_credential(api, tenant):
    created = [create_chart(api, tenant)[0] for _ in range(3)]
    root = f"/v1/projects/{tenant[0]}"
    first = api[0].get(root + "/analytics-dashboards?limit=2", headers=tenant[2]).json()
    second = (
        api[0]
        .get(
            root + "/analytics-dashboards",
            params={"after": first["next_cursor"], "limit": 2},
            headers=tenant[2],
        )
        .json()
    )
    assert {row["id"] for row in first["items"] + second["items"]} == {row["id"] for row in created}
    bucket = "analytics:" + hashlib.sha256(tenant[0].encode()).hexdigest()[:54]
    with api[2].state.sessions.begin() as session:
        session.add(
            RateBucket(key=bucket, window=int(datetime.now(UTC).timestamp() // 60), count=60)
        )
    assert read_chart(api, tenant, created[0]).status_code == 429
    new_key = api[0].post(root + "/keys", headers=api[1], json={"role": "viewer"}).json()["token"]
    assert (
        read_chart(
            api, tenant, created[0], headers={"Authorization": "Bearer " + new_key}
        ).status_code
        == 429
    )


def test_shared_saved_view_quota_and_metadata_export_project_erasure(api, tenant):
    chart, body = create_chart(api, tenant)
    root = f"/v1/projects/{tenant[0]}"
    assert read_chart(api, tenant, chart).status_code == 200
    exported = api[0].get(root + "/export", headers=api[1])
    assert exported.status_code == 200 and chart["id"] in exported.text
    with api[2].state.sessions.begin() as session:
        original = session.get(SavedView, UUID(chart["id"]))
        for _ in range(99):
            session.add(
                SavedView(
                    id=uuid4(),
                    project_id=UUID(tenant[0]),
                    environment_id=original.environment_id,
                    key="quota-" + uuid4().hex,
                    revision=1,
                    specification=original.specification,
                )
            )
    denied = api[0].post(root + "/analytics-dashboards", headers=api[1], json=chart_body())
    assert denied.status_code == 429
    project = api[0].get(root, headers=api[1]).json()
    deletion = api[0].delete(root, headers=api[1], params={"confirm_slug": project["slug"]})
    assert deletion.status_code == 200
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(SavedView)
                .where(SavedView.project_id == UUID(tenant[0]))
            )
            == 0
        )
        bucket = "analytics:" + hashlib.sha256(tenant[0].encode()).hexdigest()[:54]
        assert session.get(RateBucket, bucket) is None
    assert read_chart(api, tenant, chart, headers=api[1]).status_code == 404
    with api[2].state.sessions() as session:
        assert session.get(RateBucket, bucket) is None
