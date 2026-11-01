"""Actual authenticated React, HTTP and PostgreSQL; browser credentials remain in memory."""

import os
import subprocess
from pathlib import Path

import pytest
from integration.test_routing_program_api import prepared_program
from support_browser import browser_command
from support_http import owned_collector


@pytest.mark.postgres
def test_routing_program_approval_privacy_mobile_and_accessibility(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated routing program acceptance")
    _, _, _, row, request = prepared_program(api, tenant, tmp_path, monkeypatch)
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    root = Path(__file__).resolve().parents[2]
    with owned_collector(api[2]) as endpoint:
        completed = subprocess.run(
            browser_command(root / "web", "routing-programs.spec.ts"),
            cwd=root / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": token,
                "CUTBACK_ROUTING_PROGRAM_VIEWER": viewer,
                "CUTBACK_ROUTING_PROGRAM_TEMPLATE": row["template_plan_id"],
                "CUTBACK_ROUTING_PROGRAM_SNAPSHOT": request["baseline_snapshot_id"],
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    output = (
        (completed.stdout + completed.stderr)
        .replace(token, "<credential-omitted>")
        .replace(viewer, "<credential-omitted>")
    )
    assert completed.returncode == 0, output
