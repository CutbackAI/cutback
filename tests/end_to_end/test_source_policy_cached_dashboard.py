"""Real PG and authenticated desktop/mobile/axe coverage for the observed current reference."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from support_http import owned_collector
from support_policy_cached_program import cached_program


@pytest.mark.postgres
@pytest.mark.parametrize("requested", [False, True])
def test_cached_current_reference_scoped_approval_mobile_and_privacy(
    api, tenant, tmp_path, monkeypatch, requested
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for the authenticated current-reference browser")
    _, _, _, _, executor, calls, _, row, _, _ = cached_program(
        api, tenant, tmp_path, monkeypatch, requested=requested
    )
    admin, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    root = Path(__file__).resolve().parents[2]
    try:
        with owned_collector(api[2]) as endpoint:
            result = subprocess.run(
                browser_command(root / "web", "source-policy-current.spec.ts"),
                cwd=root / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": admin,
                    "CUTBACK_SOURCE_POLICY_VIEWER": viewer,
                    "CUTBACK_CURRENT_POLICY_PROGRAM": row["id"],
                    "CUTBACK_CURRENT_CACHE_ACTIVE": str(requested).lower(),
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-current-browser-output"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        safe = (
            (result.stdout + result.stderr).replace(admin, "<omitted>").replace(viewer, "<omitted>")
        )
        assert result.returncode == 0, safe
        assert calls == []
    finally:
        executor.close()
