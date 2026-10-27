import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update

from cutback.server.db import DailyUsage, StoredEvent
from cutback.server.operations import maintain
from cutback.server.repository import PostgresEventRepository

pytestmark = pytest.mark.postgres


def test_retention_handles_a_day_above_old_100k_limit_atomically(
    api, tenant, event, record_property
):
    client, _, app = api
    project, ingest, _ = tenant
    client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={
            "project_id": project,
            "events": [
                event.model_copy(
                    update={"traffic_kind": "application", "call_site_id": "owned-synthetic-site"}
                ).model_dump(mode="json")
            ],
        },
    )
    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
        session.execute(text("SET LOCAL statement_timeout = 60000"))
        session.execute(
            text(
                "INSERT INTO llm_events (id,event_id,project_id,environment_id,occurred_at,"
                "received_at,workflow_id,provider,model_actual,cost_usd,cost_status,cost_reason,"
                "price_snapshot_id,cost_breakdown,event) "
                "SELECT gen_random_uuid(),gen_random_uuid(),"
                "project_id,environment_id,occurred_at,received_at,workflow_id,provider,model_actual,"
                "cost_usd,cost_status,cost_reason,price_snapshot_id,cost_breakdown,event "
                "FROM llm_events CROSS JOIN generate_series(1,100000) WHERE project_id=:project"
            ),
            {"project": UUID(project)},
        )
    started = time.monotonic()
    outcome = maintain(app.state.sessions, apply=True, project_id=UUID(project))
    record_property("retention_100001_seconds", round(time.monotonic() - started, 3))
    assert outcome["raw_events_eligible"] == 100001
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == UUID(project))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(DailyUsage)
                .where(DailyUsage.project_id == UUID(project))
            )
            == 1
        )
        report = PostgresEventRepository().query_usage(
            session,
            UUID(project),
            old.replace(hour=0, minute=0, second=0, microsecond=0),
            datetime.now(UTC),
            "call_site",
            traffic_kind="application",
        )
        assert report["summary"]["observed_attempts"] == 100001
        assert report["summary"]["input_tokens_observed"] == 100001000
        assert report["summary"]["latency_p95_ms"] == 125
    assert (
        maintain(app.state.sessions, apply=True, project_id=UUID(project))["raw_events_eligible"]
        == 0
    )
