"""Authenticated observation authoring/history against a real migrated collector."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from test_pipeline import server


@pytest.mark.postgres
def test_outcome_watch_dashboard(api, tenant, tmp_path, monkeypatch):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for the authenticated browser gate")
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "tests/integration"))
    from support_outcome_watch import prepare_watch

    from cutback.server.outcome_watches import run_outcome_watch_one

    _, _, _, policy, at = prepare_watch(api, tenant, tmp_path, monkeypatch)
    result = run_outcome_watch_one(
        api[2].state.sessions, enabled=True, now=at, only_watch_id=policy["id"]
    )
    assert result["next_action"] == "REVIEW_ROLLBACK"
    admin, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    with server(api[2]) as endpoint:
        completed = subprocess.run(
            browser_command(root / "web", "outcome-watches.spec.ts"),
            cwd=root / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": admin,
                "CUTBACK_UI_VIEWER": viewer,
                "CUTBACK_WATCH_SAMPLE": "true",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    # Playwright call logs may quote fill() arguments without a Bearer prefix.
    output = (completed.stdout + completed.stderr).replace(admin, "<credential-omitted>")
    output = output.replace(viewer, "<credential-omitted>")
    assert completed.returncode == 0, output
