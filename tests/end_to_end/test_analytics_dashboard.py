"""Real scoped HTTP/PostgreSQL analytical authoring with in-memory browser credentials."""

import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from support_browser import browser_command
from support_http import owned_collector

from cutback.server.db import StoredEvent


@pytest.mark.postgres
def test_analytics_browser_lost_save_permissions_mobile_and_accessibility(
    api, tenant, event, tmp_path
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip(
            "set CUTBACK_TEST_BROWSER=true for authenticated analytical dashboard acceptance"
        )
    token = api[1]["Authorization"][7:]
    unsafe_label = "owned-<img src=x onerror=alert(1)>"
    unsafe = event.model_copy(
        update={
            "event_id": uuid4(),
            "requested_model": unsafe_label,
            "response_model": unsafe_label,
        }
    )
    refused = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={"project_id": tenant[0], "events": [unsafe.model_dump(mode="json")]},
    )
    assert refused.status_code == 422
    rows = [
        event.model_copy(update={"event_id": uuid4(), "traffic_kind": "application"}),
        event.model_copy(
            update={
                "event_id": uuid4(),
                "traffic_kind": "application",
                "requested_model": "owned-unpriced-model",
                "response_model": "owned-unpriced-model",
            }
        ),
    ]
    response = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={"project_id": tenant[0], "events": [row.model_dump(mode="json") for row in rows]},
    )
    assert response.status_code == 200, response.text
    # A legacy/untrusted stored label still has to render as text. The ordinary
    # ingestion profile above refuses it; this is owned fault-fixture setup only.
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(
                StoredEvent.project_id == UUID(tenant[0]), StoredEvent.event_id == rows[1].event_id
            )
            .values(model_actual=unsafe_label)
        )
    visual = Path(os.getenv("CUTBACK_UI_VISUAL_DIR", str(tmp_path / "private-visual")))
    visual.mkdir(parents=True, exist_ok=True)
    web = Path(__file__).resolve().parents[2] / "web"
    with owned_collector(api[2]) as endpoint:
        result = subprocess.run(
            browser_command(web, "analytics-dashboards.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_ANALYTICS": "true",
                "CUTBACK_DEMO_VIDEO": "false",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-browser"),
                "CUTBACK_UI_VISUAL_DIR": str(visual),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
    safe = (result.stdout + result.stderr).replace(token, "<omitted>")
    assert result.returncode == 0, safe
    assert "1 passed" in safe and "skipped" not in safe
