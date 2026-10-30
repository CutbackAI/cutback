"""Authenticated provider-batch preparation, exact approval and descriptive results UI."""

import os
import subprocess
from pathlib import Path

import pytest
from end_to_end.test_pipeline import server
from integration.test_provider_batch_native import approved, native_adapter, step
from integration.test_provider_batch_workflow import prepare
from support_browser import browser_command

pytestmark = pytest.mark.postgres


def test_native_batch_authenticated_dashboard_and_storage_approval(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated batch desktop/mobile/axe")
    path, completed, store = approved(api, tenant, tmp_path, monkeypatch)
    adapter, _, _ = native_adapter()
    try:
        for _ in range(24):
            outcome = step(api, store, completed["id"], adapter)
            if outcome["status"] == "COMPLETED":
                break
        assert outcome["status"] == "COMPLETED", outcome
        _, pending, _ = prepare(api, tenant, tmp_path, monkeypatch, store=store)
        other = (
            api[0].post("/v1/projects", headers=api[1], json={"slug": "batch-browser-empty"}).json()
        )
        with server(api[2]) as endpoint:
            result = subprocess.run(
                browser_command(
                    Path(__file__).resolve().parents[2] / "web", "provider-batches.spec.ts"
                ),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                    "CUTBACK_BATCH_SAMPLE": "true",
                    "CUTBACK_BATCH_PLAN": pending["id"],
                    "CUTBACK_COMPLETED_BATCH": completed["id"],
                    "CUTBACK_BATCH_OTHER_PROJECT": other["id"],
                    "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "batch-browser"),
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        adapter.close()
