"""Authenticated HTTP/PostgreSQL dashboard composition with scoped browser credentials."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from support_http import owned_collector


@pytest.mark.postgres
def test_shared_dashboard_browser_revisions_permissions_mobile_and_accessibility(
    api, tenant, tmp_path
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated shared dashboard acceptance")
    _, admin, app = api
    project = tenant[0]
    token = admin["Authorization"][7:]
    web = Path(__file__).resolve().parents[2] / "web"
    with owned_collector(app) as endpoint:
        result = subprocess.run(
            browser_command(web, "shared-dashboards.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": project,
                "CUTBACK_UI_KEY": token,
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
