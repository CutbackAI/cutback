from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sql_events
from sqlalchemy import select

from cutback.pricing import Catalog
from cutback.server.db import RateOverride, StoredEvent

pytestmark = pytest.mark.postgres


def submit(api, tenant, records):
    return api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={"project_id": tenant[0], "events": [e.model_dump(mode="json") for e in records]},
    )


def test_native_batch_database_round_trips_do_not_grow_per_event(api, tenant, event):
    def measured(records):
        calls = []

        def count(*args):
            calls.append(1)

        sql_events.listen(api[2].state.engine, "before_cursor_execute", count)
        try:
            response = submit(api, tenant, records)
            assert response.status_code == 200, response.text
            return len(calls), response.json()
        finally:
            sql_events.remove(api[2].state.engine, "before_cursor_execute", count)

    small, _ = measured([event])
    large, receipt = measured([event.model_copy(update={"event_id": uuid4()}) for _ in range(200)])
    assert receipt["accepted"] == 200
    assert large <= small + 4, (small, large)


def test_native_batches_preserve_first_observation_and_atomic_rollback(
    api, tenant, event, monkeypatch
):
    alternative = event.model_copy(update={"response_model": "unknown-owned-model"})
    receipt = submit(api, tenant, [event, alternative])
    assert (
        receipt.status_code == 200
        and receipt.json()["accepted"] == receipt.json()["duplicate"] == 1
    )
    assert submit(api, tenant, [alternative, event]).json()["duplicate"] == 2
    with api[2].state.sessions() as session:
        saved = session.scalar(select(StoredEvent).where(StoredEvent.project_id == UUID(tenant[0])))
        assert saved.model_actual == event.response_model and saved.cost_usd is not None
    records = [event.model_copy(update={"event_id": uuid4()}) for _ in range(10)]

    def fail_after_insert(*args):
        raise ValueError("OWNED_SYNTHETIC_METER_FAILURE")

    with monkeypatch.context() as context:
        context.setattr("cutback.server.billing.meter_ingestion", fail_after_insert)
        assert submit(api, tenant, records).status_code == 500
    with api[2].state.sessions() as session:
        assert not session.scalars(
            select(StoredEvent.id).where(StoredEvent.event_id.in_([r.event_id for r in records]))
        ).all()
    retry = submit(api, tenant, records)
    assert retry.status_code == 200 and retry.json()["accepted"] == 10


def test_concurrent_overlapping_native_batches_are_idempotent_in_reverse_order(api, tenant, event):
    records = [event.model_copy(update={"event_id": uuid4()}) for _ in range(100)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda i: submit(api, tenant, records if i % 2 else records[::-1]), range(6))
        )
    assert all(response.status_code == 200 for response in results)
    assert sum(response.json()["accepted"] for response in results) == 100
    assert sum(response.json()["duplicate"] for response in results) == 500


def test_bulk_rate_lookup_preserves_effective_windows_region_tier_unknown_and_tenant(
    api, tenant, event
):
    now = datetime.now(UTC)
    project = UUID(tenant[0])
    model = event.response_model
    rate_values = [
        (None, None, now - timedelta(days=10), now - timedelta(days=2), "1"),
        (None, None, now - timedelta(days=2), None, "2"),
        ("owned-region", "priority", now - timedelta(days=2), None, "3"),
        (None, None, now + timedelta(days=1), None, "999"),
    ]
    rows = []
    with api[2].state.sessions.begin() as session:
        for region, tier, start, end, price in rate_values:
            row = RateOverride(
                project_id=project,
                provider="openai",
                model=model,
                region=region,
                service_tier=tier,
                valid_from=start,
                valid_to=end,
                rates={"input": price, "output": price, "cache_read": price},
                approved_by="owned-synthetic-rates",
            )
            session.add(row)
            session.flush()
            rows.append((row.id, row.rates, start))
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-owned-rate-" + uuid4().hex})
        .json()["id"]
    )
    with api[2].state.sessions.begin() as session:
        session.add(
            RateOverride(
                project_id=UUID(other),
                provider="openai",
                model=model,
                valid_from=now - timedelta(hours=1),
                rates={"input": "999", "output": "999", "cache_read": "999"},
                approved_by="other-synthetic-project",
            )
        )
    records = [
        event.model_copy(update={"event_id": uuid4(), "occurred_at": now - timedelta(days=3)}),
        event.model_copy(update={"event_id": uuid4(), "occurred_at": now}),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "occurred_at": now,
                "region": "owned-region",
                "service_tier": "priority",
            }
        ),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "occurred_at": now,
                "region": "owned-region",
                "service_tier": None,
            }
        ),
        event.model_copy(update={"event_id": uuid4(), "response_model": "unknown-owned-model"}),
    ]
    response = submit(api, tenant, records)
    assert response.status_code == 200 and response.json()["accepted"] == 5
    with api[2].state.sessions() as session:
        saved = {
            r.event_id: r
            for r in session.scalars(select(StoredEvent).where(StoredEvent.project_id == project))
        }
        for record, (identifier, rates, start) in zip(records[:3], rows[:3], strict=True):
            expected = Catalog().price(
                record,
                {
                    **rates,
                    "snapshot_id": "project-override:" + str(identifier),
                    "effective_from": start.isoformat(),
                },
            )
            assert saved[record.event_id].price_snapshot_id == expected.snapshot_id
            assert saved[record.event_id].cost_usd == expected.amount
            assert saved[record.event_id].cost_status == "contract_rate"
        assert all(saved[r.event_id].cost_usd is None for r in records[3:])
        assert sum((saved[r.event_id].cost_usd or Decimal(0)) for r in records[:3]) > 0


def test_span_batch_and_trace_page_round_trips_stay_bounded_with_session_and_intake(
    api, tenant, request
):
    from sqlalchemy import delete, func, update
    from test_monitoring import create_rule
    from test_workflow_traces import env_id, span

    from cutback.server.monitoring_db import OnlineIntake, OnlineRule
    from cutback.server.trace_db import ExecutionSpan, SessionTrace

    def stop_owned_intake():
        with api[2].state.sessions.begin() as session:
            session.execute(
                update(OnlineRule)
                .where(OnlineRule.project_id == UUID(tenant[0]))
                .values(enabled=False)
            )
            session.execute(delete(OnlineIntake).where(OnlineIntake.project_id == UUID(tenant[0])))

    request.addfinalizer(stop_owned_intake)
    create_rule(api, tenant)
    records = [span(trace_id=uuid4().hex, session_id=f"owned-session-{i}") for i in range(200)]

    def measured(method, path, **kwargs):
        calls = []

        def count(*args):
            calls.append(1)

        sql_events.listen(api[2].state.engine, "before_cursor_execute", count)
        try:
            response = api[0].request(method, path, **kwargs)
            assert response.status_code == 200, response.text
            return len(calls), response.json()
        finally:
            sql_events.remove(api[2].state.engine, "before_cursor_execute", count)

    small, _ = measured("POST", "/v1/ingest/spans", headers=tenant[1], json={"spans": records[:1]})
    large, receipt = measured(
        "POST", "/v1/ingest/spans", headers=tenant[1], json={"spans": records}
    )
    assert receipt["accepted"] == 199 and receipt["duplicates"] == 1
    assert large <= small + 4, (small, large)
    project = UUID(tenant[0])
    with api[2].state.sessions() as session:
        for cls in (ExecutionSpan, SessionTrace, OnlineIntake):
            assert (
                session.scalar(
                    select(func.count()).select_from(cls).where(cls.project_id == project)
                )
                == 200
            )
    path = f"/v1/projects/{project}/traces"
    environment = env_id(api[2], tenant[0])
    small, _ = measured(
        "GET", path, headers=tenant[2], params={"environment_id": environment, "limit": 1}
    )
    large, page = measured(
        "GET", path, headers=tenant[2], params={"environment_id": environment, "limit": 100}
    )
    assert len(page["items"]) == 100 and large <= small + 3, (small, large)
    assert all(
        row["retained_spans"] == 1 and row["observed_execution_errors"] == 0
        for row in page["items"]
    )
