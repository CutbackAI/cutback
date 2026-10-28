"""Real PostgreSQL task CAS, canonical costs, scope and lifecycle acceptance."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from cutback.server.db import Environment, StoredEvent
from cutback.server.economics_db import TaskCostSnapshot, TaskRecord, TaskResult

pytestmark = pytest.mark.postgres


def declaration(**changes):
    return {
        "task_id": str(uuid4()),
        "result_id": str(uuid4()),
        "environment": "staging",
        "trace_id": uuid4().hex,
        "workflow": "synthetic.task",
        "service": "synthetic-shop",
        "release": "synthetic-v1",
        "occurred_at": (datetime.now(UTC) - timedelta(seconds=2)).isoformat(),
        "outcome": "success",
        "source": "app-checks",
        "source_version": "v1",
        "basis": "business_event",
        "expected_native_attempts": 0,
        "other_costs_declared_complete": True,
        **changes,
    }


def environment(api, project, name="staging"):
    with api[2].state.sessions() as session:
        return str(
            session.scalar(
                select(Environment.id).where(
                    Environment.project_id == UUID(project), Environment.name == name
                )
            )
        )


def ingest(api, tenant, *items):
    return api[0].post("/v1/ingest/task-outcomes", headers=tenant[1], json={"items": items})


def page(api, tenant, **params):
    response = api[0].get(
        f"/v1/projects/{tenant[0]}/task-outcomes",
        headers=tenant[2],
        params={"environment_id": environment(api, tenant[0]), **params},
    )
    assert response.status_code == 200, response.text
    return response.json()


def spans(api, tenant, item, **changes):
    start = int(datetime.fromisoformat(item["occurred_at"]).timestamp() * 1000000000)
    record = {
        "trace_id": item["trace_id"],
        "span_id": "1" * 16,
        "parent_span_id": None,
        "operation": "synthetic.task",
        "kind": "agent",
        "service": item["service"],
        "workflow": item["workflow"],
        "release": item["release"],
        "started_ns": start,
        "ended_ns": int(datetime.now(UTC).timestamp() * 1000000000) + 1000000000,
        "status": "unknown",
        **changes,
    }
    response = api[0].post("/v1/ingest/spans", headers=tenant[1], json={"spans": [record]})
    assert response.status_code == 200, response.text


def test_task_cas_atomic_batch_and_concurrent_replay(api, tenant):
    item = declaration()
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: ingest(api, tenant, item), range(4)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert sum(r.json()["accepted"] for r in responses) == 1
    assert sum(r.json()["duplicates"] for r in responses) == 3
    assert ingest(api, tenant, {**item, "outcome": "failure"}).status_code == 409
    second = {
        **item,
        "result_id": str(uuid4()),
        "expected_previous_revision": 1,
        "outcome": "failure",
    }
    third = {**second, "result_id": str(uuid4()), "outcome": "unknown"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda r: ingest(api, tenant, r), (second, third)))
    assert sorted(r.status_code for r in attempts) == [200, 409]
    assert page(api, tenant)["items"][0]["revision"] == 2
    newcomer = declaration()
    assert (
        ingest(
            api, tenant, newcomer, second if attempts[0].status_code != 200 else third
        ).status_code
        == 409
    )
    assert len(page(api, tenant)["items"]) == 1
    assert ingest(api, tenant, declaration(trace_id=item["trace_id"])).status_code == 409
    mutation = {
        **item,
        "result_id": str(uuid4()),
        "expected_previous_revision": 2,
        "workflow": "changed",
    }
    assert ingest(api, tenant, mutation).status_code == 409
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(TaskResult)
                .where(TaskResult.project_id == UUID(tenant[0]))
            )
            == 2
        )


def test_scope_metadata_bounds_and_shared_quotas(api, tenant, monkeypatch):
    client, admin, _ = api
    project, writer, reader = tenant
    item = declaration()
    for headers in (admin, reader):
        assert (
            client.post(
                "/v1/ingest/task-outcomes", headers=headers, json={"items": [item]}
            ).status_code
            == 403
        )
    assert ingest(api, tenant, {**item, "environment": "production"}).status_code == 403
    assert ingest(api, tenant, {**item, "raw_result": "SYNTHETIC_CONTENT"}).status_code == 422
    assert ingest(api, tenant, *[declaration() for _ in range(101)]).status_code == 422
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-" + uuid4().hex}
    ).json()["id"]
    endpoint = f"/v1/projects/{project}/task-outcomes"
    params = {"environment_id": environment(api, project)}
    assert (
        client.post(endpoint, headers=reader, params=params, json={"items": [item]}).status_code
        == 403
    )
    assert client.get(endpoint, headers=writer, params=params).status_code == 403
    assert (
        client.get(f"/v1/projects/{other}/task-outcomes", headers=reader, params=params).status_code
        == 403
    )
    assert (
        client.get(
            endpoint, headers=admin, params={"environment_id": environment(api, other)}
        ).status_code
        == 404
    )
    assert (
        client.post(endpoint, headers=admin, params=params, json={"items": [item]}).status_code
        == 200
    )
    assert (
        client.post(
            f"/v1/projects/{project}/environments", headers=admin, json={"name": "sandbox"}
        ).status_code
        == 201
    )
    sandbox = environment(api, project, "sandbox")
    assert (
        client.post(
            endpoint,
            headers=admin,
            params={"environment_id": sandbox},
            json={"items": [{**item, "environment": "sandbox"}]},
        ).status_code
        == 200
    )
    monkeypatch.setattr("cutback.server.task_economics.TASK_LIMIT", 2)
    assert ingest(api, tenant, declaration()).status_code == 409
    assert ingest(api, tenant, item).json()["duplicates"] == 1


def test_cost_joins_native_mirror_retry_unknown_and_failed_spend(api, tenant, event):
    first = declaration(expected_native_attempts=2, outcome="failure")
    second = declaration(expected_native_attempts=1)
    for item, count in ((first, 2), (second, 1)):
        spans(api, tenant, item, span_id=event.span_id, kind="llm")
        assert ingest(api, tenant, item).status_code == 200
        native = event.model_copy(
            update={
                "event_id": uuid4(),
                "trace_id": item["trace_id"],
                "traffic_kind": "application",
            }
        )
        mirror = native.model_copy(
            update={"event_id": uuid4(), "sampling_policy_id": "external-otel-spans-v1"}
        )
        events = [native, mirror]
        if count == 2:
            events.append(native.model_copy(update={"event_id": uuid4(), "attempt_number": 2}))
        response = api[0].post(
            "/v1/ingest/events",
            headers=tenant[1],
            json={
                "project_id": tenant[0],
                "events": [value.model_dump(mode="json") for value in events],
            },
        )
        assert response.status_code == 200, response.text
    response = api[0].get(
        f"/v1/projects/{tenant[0]}/task-economics",
        headers=tenant[2],
        params={
            "environment_id": environment(api, tenant[0]),
            "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "end": datetime.now(UTC).isoformat(),
            "expected_tasks": 2,
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    items = {r["declaration"]["task_id"]: r["economics"] for r in report["items"]}
    failed = items[first["task_id"]]
    assert failed["observed_native_attempts"] == failed["priced_native_attempts"] == 2
    known = Decimal(failed["known_native_modeled_subtotal_usd"])
    success = Decimal(items[second["task_id"]]["known_native_modeled_subtotal_usd"])
    assert known == success * 2
    assert Decimal(report["summary"]["declared_window_cost_per_success_usd"]) == known + success
    assert report["summary"]["production_savings_usd"] is None
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.project_id == UUID(tenant[0]), StoredEvent.event_id == native.event_id
            )
            .values(cost_usd=None, cost_status="unknown")
        )
    unknown = next(
        i for i in page(api, tenant)["items"] if i["declaration"]["task_id"] == second["task_id"]
    )
    assert unknown["economics"]["declared_modeled_total_usd"] is None
    assert unknown["economics"]["known_native_modeled_subtotal_usd"] is None


def test_late_trace_integrity_and_declaration_watermark(api, tenant):
    item = declaration()
    assert ingest(api, tenant, item).status_code == 200
    first = page(api, tenant)
    assert first["items"][0]["economics"]["declared_modeled_total_usd"] is None
    spans(api, tenant, item)
    retained = page(api, tenant)
    assert Decimal(retained["items"][0]["economics"]["declared_modeled_total_usd"]) == 0
    changed = {
        **item,
        "result_id": str(uuid4()),
        "expected_previous_revision": 1,
        "outcome": "failure",
    }
    assert ingest(api, tenant, changed).status_code == 200
    assert page(api, tenant)["items"][0]["declaration"]["outcome"] == "failure"
    assert (
        page(api, tenant, as_of=first["declarations_as_of"])["items"][0]["declaration"]["outcome"]
        == "success"
    )
    # Watermark freezes declarations only: newly retained trace evidence is explicit.
    assert "not_frozen_telemetry" in retained["evidence_scope"]
    orphan = declaration()
    assert ingest(api, tenant, orphan).status_code == 200
    spans(api, tenant, orphan, parent_span_id="2" * 16)
    found = next(
        i for i in page(api, tenant)["items"] if i["declaration"]["task_id"] == orphan["task_id"]
    )
    assert "root_not_observed" in found["trace_integrity"]
    assert found["economics"]["declared_modeled_total_usd"] is None


def test_export_retention_pruning_and_project_delete(api, tenant):
    from cutback.server.operations import maintain

    item = declaration(occurred_at=(datetime.now(UTC) - timedelta(days=40)).isoformat())
    assert ingest(api, tenant, item).status_code == 200
    response = api[0].get(f"/v1/projects/{tenant[0]}/export", headers=api[1])
    assert response.status_code == 200
    assert '"table": "application_tasks"' in response.text
    assert '"table": "application_task_results"' in response.text
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    assert page(api, tenant)["items"] == []
    assert ingest(api, tenant, declaration()).status_code == 200
    with api[2].state.sessions() as session:
        from cutback.server.db import Project

        slug = session.get(Project, UUID(tenant[0])).slug
    response = api[0].delete(
        f"/v1/projects/{tenant[0]}", headers=api[1], params={"confirm_slug": slug}
    )
    assert response.status_code == 200, response.text
    with api[2].state.sessions() as session:
        for cls in (TaskRecord, TaskResult, TaskCostSnapshot):
            assert (
                session.scalar(
                    select(func.count()).select_from(cls).where(cls.project_id == UUID(tenant[0]))
                )
                == 0
            )


def test_frozen_receipt_idempotence_scope_late_data_and_retention(api, tenant, event):
    client, admin, app = api
    item = declaration(expected_native_attempts=1)
    spans(api, tenant, item, span_id=event.span_id)
    assert ingest(api, tenant, item).status_code == 200
    path = f"/v1/projects/{tenant[0]}/task-cost-snapshots"
    params = {"environment_id": environment(api, tenant[0])}
    body = {
        "snapshot_id": str(uuid4()),
        "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
        "expected_tasks": 1,
    }
    assert client.post(path, params=params, headers=tenant[2], json=body).status_code == 403
    first = client.post(path, params=params, headers=admin, json=body)
    assert first.status_code == 200, first.text
    frozen = first.json()
    assert frozen["report"]["summary"]["declared_sample_total_usd"] is None
    native = event.model_copy(update={"trace_id": item["trace_id"], "traffic_kind": "application"})
    assert (
        client.post(
            "/v1/ingest/events",
            headers=tenant[1],
            json={"project_id": tenant[0], "events": [native.model_dump(mode="json")]},
        ).status_code
        == 200
    )
    assert page(api, tenant)["items"][0]["economics"]["declared_cost_complete"]
    duplicate = client.post(path, params=params, headers=admin, json=body)
    assert duplicate.status_code == 200 and duplicate.json() == frozen
    assert (
        client.post(
            path, params=params, headers=admin, json={**body, "expected_tasks": 2}
        ).status_code
        == 409
    )
    read = client.get(path + "/" + frozen["id"], headers=tenant[2])
    assert read.status_code == 200 and read.json() == frozen
    listing = client.get(path, params=params, headers=tenant[2])
    assert listing.status_code == 200, listing.text
    assert listing.json()["items"][0]["summary"] == frozen["report"]["summary"]
    assert "report" not in listing.json()["items"][0]
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.get(
            f"/v1/projects/{other}/task-cost-snapshots/{frozen['id']}", headers=admin
        ).status_code
        == 404
    )
    exported = client.get(f"/v1/projects/{tenant[0]}/export", headers=admin)
    assert '"table": "task_cost_snapshots"' in exported.text
    with app.state.sessions.begin() as session:
        session.execute(
            update(TaskCostSnapshot)
            .where(TaskCostSnapshot.project_id == UUID(tenant[0]))
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )
    from cutback.server.operations import maintain

    maintain(app.state.sessions, project_id=UUID(tenant[0]), apply=True)
    assert client.get(path + "/" + frozen["id"], headers=tenant[2]).status_code == 404


def test_snapshot_concurrent_identity_and_bounded_graph_coverage(api, tenant, monkeypatch):
    for _ in range(2):
        item = declaration()
        spans(api, tenant, item)
        assert ingest(api, tenant, item).status_code == 200
    path = f"/v1/projects/{tenant[0]}/task-cost-snapshots"
    request = {
        "snapshot_id": str(uuid4()),
        "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
        "expected_tasks": 2,
    }
    params = {"environment_id": environment(api, tenant[0])}
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(
            pool.map(
                lambda _: api[0].post(path, headers=api[1], params=params, json=request), range(3)
            )
        )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len({r.json()["id"] for r in responses}) == 1
    monkeypatch.setattr("cutback.server.task_economics.GRAPH_WINDOW_LIMIT", 1)
    report = page(api, tenant)
    assert all("span_page_truncated" in i["trace_integrity"] for i in report["items"])
    assert all(i["economics"]["declared_modeled_total_usd"] is None for i in report["items"])


def test_native_parent_start_correlates_without_inventing_monotonic_utc_end(api, tenant, event):
    item = declaration(expected_native_attempts=1)
    parent = "1" * 16
    stamp = event.occurred_at
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    ns = ((stamp - epoch) // timedelta(microseconds=1)) * 1000
    spans(api, tenant, item, span_id=parent, started_ns=ns - 1000, ended_ns=ns + 1000)
    assert ingest(api, tenant, item).status_code == 200
    native = event.model_copy(
        update={
            "trace_id": item["trace_id"],
            "parent_span_id": parent,
            "traffic_kind": "application",
            "duration_ms": 125,
        }
    )
    assert (
        api[0]
        .post(
            "/v1/ingest/events",
            headers=tenant[1],
            json={"project_id": tenant[0], "events": [native.model_dump(mode="json")]},
        )
        .status_code
        == 200
    )
    report = page(api, tenant)["items"][0]
    assert report["economics"]["declared_cost_complete"]
    assert report["trace_integrity"] == ["capture_coverage_unknown"]


def test_unreceipted_llm_step_cannot_be_declared_zero_cost(api, tenant):
    item = declaration(expected_native_attempts=0)
    spans(api, tenant, item, kind="llm")
    assert ingest(api, tenant, item).status_code == 200
    report = page(api, tenant)["items"][0]
    assert "llm_span_without_application_attempt_receipt" in report["trace_integrity"]
    assert report["economics"]["declared_modeled_total_usd"] is None
    assert report["economics"]["known_native_modeled_subtotal_usd"] is None
