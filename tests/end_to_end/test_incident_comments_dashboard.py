"""Authenticated incident discussion using the real collector and encrypted store."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from support_http import owned_collector
from support_incident_comments import collaboration as collaboration


@pytest.mark.postgres
def test_incident_comments_browser_encryption_revisions_revocation_mobile_and_accessibility(
    api, tenant, collaboration, tmp_path
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated comments acceptance")
    token = api[1]["Authorization"][7:]
    web = Path(__file__).resolve().parents[2] / "web"
    with owned_collector(api[2]) as endpoint:
        result = subprocess.run(
            browser_command(web, "incident-comments.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_INCIDENT": str(collaboration["incident"]),
                "CUTBACK_DEMO_VIDEO": "false",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-browser"),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
    safe = (result.stdout + result.stderr).replace(token, "<omitted>")
    assert result.returncode == 0, safe
    assert "1 passed" in safe and "skipped" not in safe
