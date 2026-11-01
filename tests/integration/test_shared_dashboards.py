"""Real scoped PostgreSQL compositions, pinned filters, conflicts and bounded evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_monitoring import alert, create_rule, ingest, span_body

from cutback.server.db import AuditEvent
from cutback.server.monitoring_db import Incident, SavedView

pytestmark = pytest.mark.postgres


def view(api, tenant, **changes):
    body = {
        "view_id": str(uuid4()),
        "key": "owned-" + uuid4().hex,
        "expected_revision": 0,
        "environment": "staging",
        "page": "traces",
        "filter": {"workflow": "synthetic-support"},
        **changes,
    }
    result = api[0].post(f"/v1/projects/{tenant[0]}/saved-views", headers=api[1], json=body)
    assert result.status_code == 201, result.text
    return result.json(), body


def body_for(*views, **changes):
    return {
        "dashboard_id": str(uuid4()),
        "key": "team-" + uuid4().hex,
        "expected_revision": 0,
        "environment": "staging",
        "panels": [{"view_id": row["id"], "revision": row["revision"]} for row in views],
        **changes,
    }


def create(api, tenant, body):
    result = api[0].post(f"/v1/projects/{tenant[0]}/dashboards", headers=api[1], json=body)
    assert result.status_code == 201, result.text
    return result.json()


def test_dashboard_sharing_current_permissions_idempotence_audit_and_typed_metadata(api, tenant):
    source, _ = view(api, tenant)
    body = body_for(source)
    base = f"/v1/projects/{tenant[0]}"
    assert api[0].post(base + "/dashboards", headers=tenant[2], json=body).status_code == 403
    assert api[0].post(base + "/dashboards", headers=tenant[1], json=body).status_code == 403
    created = create(api, tenant, body)
    assert create(api, tenant, body) == created
    assert created["panels"][0]["source_state"] == "current"
    assert created["content_included"] is False and created["population_coverage"] == "unknown"
    assert (
        api[0].get(base + "/dashboards", headers=tenant[2]).json()["items"][0]["id"]
        == created["id"]
    )
    assert len(api[0].get(base + "/saved-views", headers=tenant[2]).json()["items"]) == 1
    assert api[0].get(base + "/dashboards/" + created["id"], headers=tenant[1]).status_code == 403
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.project_id == UUID(tenant[0]),
                    AuditEvent.action == "shared_dashboard_updated",
                )
            )
            == 1
        )


def test_pinned_views_survive_change_deletion_and_recreated_identity_without_silent_retargeting(
    api, tenant
):
    source, original = view(api, tenant)
    dashboard = create(api, tenant, body_for(source))
    path = f"/v1/projects/{tenant[0]}/dashboards/" + dashboard["id"]
    updated = {**original, "expected_revision": 1, "filter": {"workflow": "different-workflow"}}
    assert (
        api[0]
        .post(f"/v1/projects/{tenant[0]}/saved-views", headers=api[1], json=updated)
        .status_code
        == 201
    )
    changed = api[0].get(path, headers=tenant[2]).json()
    assert changed["panels"][0]["source_state"] == "changed"
    assert changed["panels"][0]["filter"]["workflow"] == "synthetic-support"
    assert changed["definition_sha256"] == dashboard["definition_sha256"]
    source_path = f"/v1/projects/{tenant[0]}/saved-views/" + source["id"]
    assert api[0].delete(source_path + "?expected_revision=2", headers=api[1]).status_code == 200
    assert api[0].get(path, headers=tenant[2]).json()["panels"][0]["source_state"] == "deleted"
    view(api, tenant, **{**updated, "expected_revision": 0})
    assert api[0].get(path, headers=tenant[2]).json()["panels"][0]["source_state"] == "changed"


def test_simultaneous_team_edits_have_one_winner_and_stale_delete_is_refused(api, tenant):
    sources = [view(api, tenant)[0] for _ in range(2)]
    body = body_for(*sources)
    dashboard = create(api, tenant, body)
    path = f"/v1/projects/{tenant[0]}/dashboards"
    variants = [{**body, "expected_revision": 1, "panels": [body["panels"][i]]} for i in range(2)]
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda value: api[0].post(path, headers=api[1], json=value), variants)
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    target = path + "/" + dashboard["id"]
    assert (
        api[0]
        .get(target + "/panels/" + sources[0]["id"] + "?expected_revision=1", headers=tenant[2])
        .status_code
        == 409
    )
    assert api[0].delete(target + "?expected_revision=1", headers=api[1]).status_code == 409
    assert api[0].delete(target + "?expected_revision=2", headers=tenant[2]).status_code == 403
    assert api[0].delete(target + "?expected_revision=2", headers=api[1]).status_code == 200
    assert api[0].get(target, headers=tenant[2]).status_code == 404


def test_foreign_views_environments_changed_revisions_nesting_and_unknown_fields_are_refused(
    api, tenant
):
    source, original = view(api, tenant)
    body = body_for(source)
    path = f"/v1/projects/{tenant[0]}/dashboards"
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
        .json()["id"]
    )
    assert api[0].post(path.replace(tenant[0], other), headers=api[1], json=body).status_code == 404
    bad = {**body, "panels": [{"view_id": source["id"], "revision": 2}]}
    assert api[0].post(path, headers=api[1], json=bad).status_code == 409
    for bad in (
        {**body, "panels": body["panels"] * 2},
        {**body, "raw_query": "SYNTHETIC_SECRET"},
        {**body, "panels": body["panels"] * 9},
        {**body, "dashboard_id": str(UUID(int=0))},
    ):
        result = api[0].post(path, headers=api[1], json=bad)
        assert result.status_code == 422 and "SYNTHETIC_SECRET" not in result.text
    dashboard = create(api, tenant, body)
    nested = body_for({"id": dashboard["id"], "revision": 1})
    assert api[0].post(path, headers=api[1], json=nested).status_code == 422
    collision = {
        **original,
        "view_id": dashboard["id"],
        "key": dashboard["key"],
        "expected_revision": 1,
    }
    assert (
        api[0]
        .post(f"/v1/projects/{tenant[0]}/saved-views", headers=api[1], json=collision)
        .status_code
        == 409
    )
    assert (
        api[0]
        .delete(
            f"/v1/projects/{tenant[0]}/saved-views/" + dashboard["id"] + "?expected_revision=1",
            headers=api[1],
        )
        .status_code
        == 404
    )


def test_span_panels_apply_all_pinned_filters_server_side_and_paginate_without_counts_or_content(
    api, tenant
):
    source, _ = view(
        api,
        tenant,
        filter={
            "workflow": "synthetic-support",
            "service": "synthetic-desk",
            "kind": "chain",
            "release": "owned-v1",
        },
    )
    dashboard = create(api, tenant, body_for(source))
    ingest(
        api,
        tenant,
        [span_body(release="owned-v1") for _ in range(3)]
        + [
            {**span_body(release="owned-v1"), "workflow": "unrelated"},
            span_body(release="owned-v2"),
        ],
    )
    path = (
        f"/v1/projects/{tenant[0]}/dashboards/{dashboard['id']}/panels/{source['id']}"
        "?expected_revision=1"
    )
    first = api[0].get(path + "&limit=2", headers=tenant[2]).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = api[0].get(path + "&limit=2&after=" + first["next_cursor"], headers=tenant[2]).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert len({row["id"] for row in first["items"] + second["items"]}) == 3
    assert all(
        row["release"] == "owned-v1" and row["span_kind"] == "chain" for row in first["items"]
    )
    assert first["retention_coverage"] == first["population_coverage"] == "unknown"
    assert (
        first["content_included"] is False and first["selection_basis"] == "matching_retained_spans"
    )
    assert "total" not in first and "quantiles" not in first
    assert (
        api[0].get(path.replace(source["id"], str(uuid4())), headers=tenant[2]).status_code == 404
    )


def test_incident_panels_match_declared_rule_filters_and_state_without_claiming_task_success(
    api, tenant
):
    source, _ = view(api, tenant, page="incidents", incident_state="open")
    rule = create_rule(api, tenant)
    alarm = alert(api, tenant, rule)
    now = datetime.now(UTC)
    with api[2].state.sessions.begin() as session:
        session.add_all(
            [
                Incident(
                    project_id=UUID(tenant[0]),
                    alert_rule_id=UUID(alarm["id"]),
                    window_start=now - timedelta(minutes=i + 1),
                    window_end=now - timedelta(minutes=i),
                    state=state,
                    evidence={},
                )
                for i, state in enumerate(("open", "resolved"))
            ]
        )
    dashboard = create(api, tenant, body_for(source))
    path = (
        f"/v1/projects/{tenant[0]}/dashboards/{dashboard['id']}/panels/{source['id']}"
        "?expected_revision=1"
    )
    result = api[0].get(path, headers=tenant[2])
    assert result.status_code == 200, result.text
    data = result.json()
    assert len(data["items"]) == 1 and data["items"][0]["incident_state"] == "open"
    assert data["selection_basis"] == "matching_incident_rule_filters"


def test_shared_storage_quota_is_atomic_and_project_export_deletion_includes_dashboards(
    api, tenant
):
    source, _ = view(api, tenant)
    with api[2].state.sessions.begin() as session:
        original = session.get(SavedView, UUID(source["id"]))
        session.add_all(
            [
                SavedView(
                    id=uuid4(),
                    project_id=original.project_id,
                    environment_id=original.environment_id,
                    key="quota-" + str(i),
                    revision=1,
                    specification=original.specification,
                )
                for i in range(99)
            ]
        )
    path = f"/v1/projects/{tenant[0]}"
    body = body_for(source)
    assert api[0].post(path + "/dashboards", headers=api[1], json=body).status_code == 429
    with api[2].state.sessions.begin() as session:
        row = session.scalar(
            select(SavedView).where(
                SavedView.project_id == UUID(tenant[0]), SavedView.key == "quota-0"
            )
        )
        session.delete(row)
    dashboard = create(api, tenant, body)
    exported = api[0].get(path + "/export", headers=api[1])
    assert exported.status_code == 200 and dashboard["id"] in exported.text
    project = api[0].get(path, headers=api[1]).json()
    assert (
        api[0].delete(path, headers=api[1], params={"confirm_slug": project["slug"]}).status_code
        == 200
    )
    with api[2].state.sessions() as session:
        assert session.get(SavedView, UUID(dashboard["id"])) is None


def test_dashboard_list_has_keyset_pagination_and_live_key_revocation(api, tenant):
    source, _ = view(api, tenant)
    dashboards = [create(api, tenant, body_for(source)) for _ in range(3)]
    base = f"/v1/projects/{tenant[0]}"
    seen = []
    cursor = None
    for _ in range(4):
        page = (
            api[0]
            .get(
                base + "/dashboards?limit=1" + ("&after=" + cursor if cursor else ""),
                headers=tenant[2],
            )
            .json()
        )
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert sorted(seen) == sorted(row["id"] for row in dashboards)
    key = api[0].post(base + "/keys", headers=api[1], json={"role": "viewer"}).json()
    headers = {"Authorization": "Bearer " + key["token"]}
    assert api[0].get(base + "/dashboards", headers=headers).status_code == 200
    assert api[0].delete(base + "/keys/" + key["key_id"], headers=api[1]).status_code == 200
    assert api[0].get(base + "/dashboards", headers=headers).status_code == 401


def test_dashboard_cannot_mix_environment_views_or_retarget_an_existing_pinned_panel(api, tenant):
    base = f"/v1/projects/{tenant[0]}"
    result = api[0].post(base + "/environments", headers=api[1], json={"name": "production"})
    assert result.status_code == 201, result.text
    staging, _ = view(api, tenant)
    production, _ = view(api, tenant, environment="production")
    body = body_for(staging, production)
    refused = api[0].post(base + "/dashboards", headers=api[1], json=body)
    assert refused.status_code == 422
    assert refused.json()["code"] == "DASHBOARD_SINGLE_ENVIRONMENT_REQUIRED"
    assert api[0].get(base + "/dashboards", headers=tenant[2]).json()["items"] == []


def test_independent_demo_seeds_shared_dashboard_via_real_http_without_content_or_inference(
    api, tenant
):
    import httpx
    from support_http import owned_collector

    from cutback.demo_shared_dashboards import seed_shared_dashboard

    ingest(api, tenant, [span_body()])
    with owned_collector(api[2]) as endpoint:
        with httpx.Client(base_url=endpoint, headers=api[1], trust_env=False) as client:
            receipt = seed_shared_dashboard(client, tenant[0], "synthetic-support")
    assert receipt["matching_retained_spans_verified"] is True
    assert receipt["panels"] == 2 and receipt["paid_provider_calls"] == 0
    assert receipt["content_included"] is False and receipt["population_coverage"] == "unknown"


def test_scoped_editor_can_update_dashboard_and_immediate_revocation_stops_further_edits(
    api, tenant
):
    base = f"/v1/projects/{tenant[0]}"
    source, _ = view(api, tenant)
    key = api[0].post(base + "/keys", headers=api[1], json={"role": "editor"}).json()
    editor = {"Authorization": "Bearer " + key["token"]}
    body = body_for(source)
    created = api[0].post(base + "/dashboards", headers=editor, json=body)
    assert created.status_code == 201, created.text
    changed = api[0].post(
        base + "/dashboards", headers=editor, json={**body, "expected_revision": 1}
    )
    assert changed.status_code == 201 and changed.json()["revision"] == 2
    assert api[0].delete(base + "/keys/" + key["key_id"], headers=api[1]).status_code == 200
    assert (
        api[0]
        .post(base + "/dashboards", headers=editor, json={**body, "expected_revision": 2})
        .status_code
        == 401
    )
    retained = api[0].get(base + "/dashboards/" + body["dashboard_id"], headers=tenant[2])
    assert retained.status_code == 200 and retained.json()["revision"] == 2
