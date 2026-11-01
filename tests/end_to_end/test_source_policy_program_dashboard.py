"""Authenticated React and PostgreSQL gate for common-policy preparation and approval."""

import os
import subprocess
from pathlib import Path

import pytest
from integration.test_source_policy_program_api import prepared_program
from support_browser import browser_command
from support_http import owned_collector


@pytest.mark.postgres
def test_common_policy_budget_privacy_mobile_and_accessibility(api, tenant, tmp_path, monkeypatch):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for common-policy browser acceptance")
    _, _, _, _, executor, calls, _, row, request = prepared_program(
        api, tenant, tmp_path, monkeypatch
    )
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    root = Path(__file__).resolve().parents[2]
    try:
        with owned_collector(api[2]) as endpoint:
            result = subprocess.run(
                browser_command(root / "web", "source-policy-programs.spec.ts"),
                cwd=root / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_SOURCE_POLICY_VIEWER": viewer,
                    "CUTBACK_SOURCE_POLICY_TEMPLATE": row["template_plan_id"],
                    "CUTBACK_SOURCE_POLICY_SNAPSHOT": request["baseline_snapshot_id"],
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-browser-output"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        safe = (
            (result.stdout + result.stderr).replace(token, "<omitted>").replace(viewer, "<omitted>")
        )
        assert result.returncode == 0, safe
        assert calls == []
    finally:
        executor.close()
