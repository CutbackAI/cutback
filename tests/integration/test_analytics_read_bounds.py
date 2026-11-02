"""Real concurrent commits, cardinality and PostgreSQL deadlines under the chart contract."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from integration.test_analytics_dashboards import chart_body, create_chart, ingest, read_chart
from sqlalchemy import select, text, update

from cutback.server.analytics_api import PostgresEventRepository
from cutback.server.db import StoredEvent
from cutback.server.operations import maintain

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    # Late arrivals deliberately leave an archived day needing reconciliation.
    # Keep these fault fixtures out of the shared retention-maintenance database.
    return isolated_postgres_url


def test_one_read_keeps_a_snapshot_when_another_connection_commits_between_panels(
    api, tenant, event, monkeypatch
):
    original = PostgresEventRepository.query_usage
    calls = []
    ingest(api, tenant, event.model_copy(update={"traffic_kind": "application"}))
    chart, _ = create_chart(api, tenant)

    def competing_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            # Same occurrence window, different committed transaction: every
            # panel in the first read must continue to see only the first event.
            ingest(
                api,
                tenant,
                event.model_copy(update={"event_id": uuid4(), "traffic_kind": "application"}),
            )
        return result

    monkeypatch.setattr(PostgresEventRepository, "query_usage", competing_commit)
    result = read_chart(api, tenant, chart)
    assert result.status_code == 200
    assert {panel["summary"]["observed_attempts"] for panel in result.json()["panels"]} == {1}
    # Three panels, two distinct selections; reusing the daily selection must
    # not create an extra database query or a fresh snapshot for the last panel.
    assert len(calls) == 2
    refreshed = read_chart(api, tenant, chart)
    assert refreshed.status_code == 200
    assert {panel["summary"]["observed_attempts"] for panel in refreshed.json()["panels"]} == {2}


def test_dimension_bound_keeps_complete_summary_and_explicit_truncation(api, tenant, event):
    events = [
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": f"owned-unpriced-{number}",
                "response_model": f"owned-unpriced-{number}",
            }
        )
        for number in range(101)
    ]
    ingest(api, tenant, *events[:100])
    ingest(api, tenant, events[100])
    chart, _ = create_chart(api, tenant)
    result = read_chart(api, tenant, chart)
    assert result.status_code == 200
    costs = next(panel for panel in result.json()["panels"] if panel["key"] == "cost")
    assert len(costs["points"]) == 100 and costs["points_truncated"] is True
    assert costs["summary"]["observed_attempts"] == 101
    assert costs["summary"]["unpriced_attempts"] == 101
    assert costs["summary"]["modeled_cost_usd"] is None


def test_actual_postgresql_statement_timeout_returns_no_partial_dashboard(api, tenant, monkeypatch):
    chart, _ = create_chart(api, tenant)

    def slow_statement(self, session, *args, **kwargs):
        session.execute(text("SELECT pg_sleep(6)"))
        pytest.fail("ANALYTICS_SQL_DEADLINE_WAS_NOT_ENFORCED")

    monkeypatch.setattr(PostgresEventRepository, "query_usage", slow_statement)
    result = read_chart(api, tenant, chart)
    assert result.status_code == 503
    assert result.json()["code"] == "ANALYTICS_QUERY_UNAVAILABLE"
    assert "panels" not in result.json() and "pg_sleep" not in result.text


def test_retained_group_promoted_into_a_truncated_page_includes_its_current_raw_attempts(
    api, tenant, event
):
    target = "owned-retained-model"
    old = [
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": target,
                "response_model": target,
            }
        )
        for _ in range(3)
    ]
    ingest(api, tenant, *old)
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.project_id == UUID(tenant[0]),
            )
            .values(occurred_at=datetime.now(UTC) - timedelta(days=40))
        )
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    # The current target's single attempt is outside the raw top 100. Its three
    # retained attempts promote it into the combined page; neither part may be lost.
    raw = [
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": f"owned-current-{number}",
                "response_model": f"owned-current-{number}",
            }
        )
        for number in range(100)
        for _ in range(2)
    ]
    raw.append(old[0].model_copy(update={"event_id": uuid4()}))
    for offset in range(0, len(raw), 100):
        ingest(api, tenant, *raw[offset : offset + 100])
    chart, _ = create_chart(api, tenant, chart_body(window_days=90))
    response = read_chart(api, tenant, chart)
    assert response.status_code == 200
    costs = next(panel for panel in response.json()["panels"] if panel["key"] == "cost")
    assert costs["points_truncated"] is True and len(costs["points"]) == 100
    assert costs["summary"]["observed_attempts"] == 204
    selected = next(point for point in costs["points"] if point["key"] == target)
    assert selected["observed_attempts"] == 4
    assert selected["unpriced_attempts"] == 4 and selected["modeled_cost_usd"] is None


def test_missing_model_and_retained_unknown_model_label_keep_every_observed_attempt(
    api, tenant, event
):
    ingest(
        api,
        tenant,
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": None,
                "response_model": None,
            }
        ),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": "unknown_model",
                "response_model": "unknown_model",
            }
        ),
    )
    chart, _ = create_chart(api, tenant)
    response = read_chart(api, tenant, chart)
    assert response.status_code == 200
    costs = next(panel for panel in response.json()["panels"] if panel["key"] == "cost")
    assert costs["summary"]["observed_attempts"] == 2
    assert len(costs["points"]) == 1 and costs["points"][0]["key"] == "unknown_model"
    assert costs["points"][0]["observed_attempts"] == 2
    assert costs["points"][0]["unpriced_attempts"] == 2


def test_retained_date_group_uses_typed_keys_and_preserves_exact_known_cost(api, tenant, event):
    old = [event.model_copy(update={"event_id": uuid4()}) for _ in range(3)]
    ingest(api, tenant, *old)
    with api[2].state.sessions.begin() as session:
        single_cost = session.scalar(
            select(StoredEvent.cost_usd).where(StoredEvent.project_id == UUID(tenant[0])).limit(1)
        )
        assert single_cost is not None
        old_day = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(tenant[0]))
            .values(occurred_at=old_day)
        )
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    current = [event.model_copy(update={"event_id": uuid4()}) for _ in range(3)]
    ingest(api, tenant, *current)
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.project_id == UUID(tenant[0]),
                StoredEvent.event_id == current[-1].event_id,
            )
            .values(occurred_at=old_day)
        )
    with api[2].state.sessions() as session:
        report = PostgresEventRepository().query_usage(
            session,
            UUID(tenant[0]),
            datetime.now(UTC) - timedelta(days=90),
            datetime.now(UTC),
            "day",
            environment_name="staging",
            group_limit=1,
        )
    assert report["groups_truncated"] is True and len(report["groups"]) == 1
    assert report["summary"]["observed_attempts"] == 6
    assert Decimal(report["summary"]["known_modeled_cost_subtotal_usd"]) == single_cost * 6
    selected = report["groups"][0]
    assert selected["observed_attempts"] == 4
    assert Decimal(selected["known_modeled_cost_subtotal_usd"]) == single_cost * 4
    assert selected["latency_p95_ms"] is None
