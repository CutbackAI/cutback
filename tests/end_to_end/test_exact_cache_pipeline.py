"""Actual HTTP/PostgreSQL/native SDK cache experiment and authenticated UI/axe."""

import os
import subprocess
from pathlib import Path

import httpx
import pytest
from support_browser import browser_command
from support_exact_cache import prepare_cache_experiment
from test_pipeline import server

from cutback.server.worker import run_one

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_exact_cache_experiment_real_http_and_authenticated_browser(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for actual cache/browser acceptance")
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, trust_env=False, timeout=30) as client,
    ):
        store, run_id, executor, calls = prepare_cache_experiment(
            (client, api[1], api[2]), tenant, tmp_path, monkeypatch
        )
        try:
            result = run_one(
                api[2].state.sessions,
                store,
                executor=executor,
                execution_enabled=True,
                only_run_id=run_id,
            )
            assert result["status"] == "COMPLETED" and len(calls) == 9
        finally:
            executor.close()
        admin, viewer = (headers["Authorization"][7:] for headers in (api[1], tenant[2]))
        web = Path(__file__).resolve().parents[2] / "web"
        checked = subprocess.run(
            browser_command(web, "exact-cache.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": admin,
                "CUTBACK_CACHE_VIEWER": viewer,
                "CUTBACK_EXACT_CACHE_BROWSER": "true",
                "CUTBACK_DEMO_VIDEO": "false",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "cache-browser"),
            },
            capture_output=True,
            text=True,
            timeout=150,
        )
        safe = (
            (checked.stdout + checked.stderr)
            .replace(admin, "<credential-omitted>")
            .replace(viewer, "<credential-omitted>")
        )
        assert checked.returncode == 0, safe
