from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from cutback.server.db import StoredEvent
from cutback.server.operations import maintain
from cutback.server.repository import PostgresEventRepository

pytestmark = pytest.mark.postgres


def test_traffic_and_callsite_totals_reconcile_before_and_after_retention(api, tenant, event):
    client, _, app = api
    project, ingest, viewer = tenant
    entries = [
        event.model_copy(
            update={"event_id": uuid4(), "traffic_kind": kind, "call_site_id": "callsite:owned"}
        )
        for kind in ("application", "evaluation", "judge", "unknown")
    ]
    response = client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [row.model_dump(mode="json") for row in entries]},
    )
    assert response.status_code == 200
    report = client.get(f"/v1/projects/{project}/usage?group_by=traffic", headers=viewer).json()
    assert report["summary"]["observed_attempts"] == 4
    assert {row["key"] for row in report["groups"]} == {
        "application",
        "evaluation",
        "judge",
        "unknown",
    }
    app_only = client.get(
        f"/v1/projects/{project}/usage?traffic_kind=application&group_by=call_site", headers=viewer
    ).json()
    assert (
        app_only["summary"]["observed_attempts"] == 1
        and app_only["groups"][0]["key"] == "callsite:owned"
    )
    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
    assert (
        maintain(app.state.sessions, project_id=UUID(project), apply=True)["raw_events_eligible"]
        == 4
    )
    with app.state.sessions() as session:
        report = PostgresEventRepository().query_usage(
            session,
            UUID(project),
            old.replace(hour=0, minute=0, second=0, microsecond=0),
            datetime.now(UTC),
            "call_site",
            traffic_kind="application",
        )
    assert report["summary"]["observed_attempts"] == 1
    assert report["groups"][0]["key"] == "callsite:owned"
