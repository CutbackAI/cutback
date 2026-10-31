"""Real HTTP, native fixture SDK, PostgreSQL and authenticated desktop/mobile evidence."""

import os
import subprocess
from pathlib import Path

import httpx
import pytest
from support_agent import native_response, prepare_fresh
from support_browser import browser_command
from test_pipeline import server

from cutback.agent import advance
from cutback.evals.executors import OpenAIExecutor
from cutback.server.worker import run_one

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_native_source_fixture_real_http_and_authenticated_browser(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for actual authenticated fixture evidence")
    with server(api[2]) as endpoint, httpx.Client(trust_env=False, timeout=30) as client:
        root, control, state, _ = prepare_fresh(
            (client, api[1], api[2]),
            tenant,
            tmp_path,
            monkeypatch,
            fixture_transport=True,
            endpoint=endpoint,
        )
        queued = advance(
            root,
            control,
            plan_hash=state["plan"]["plan_hash"],
            approval_ref="owned-browser-fixture",
        )
        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
        native = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(native_response), trust_env=False
            )
        )

        class FixtureExecutor:
            synthetic_transport = True

            def execute(self, model, text, timeout_seconds):
                return native.execute(model, text, timeout_seconds)

        try:
            result = run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=FixtureExecutor(),
                execution_enabled=True,
                only_run_id=queued["run_id"],
            )
            assert result["status"] == "COMPLETED"
        finally:
            native.close()
        assert advance(root, control)["phase"] == "COMPLETED"
        web = Path(__file__).resolve().parents[2] / "web"
        admin, viewer = (
            headers["Authorization"].removeprefix("Bearer ") for headers in (api[1], tenant[2])
        )
        checked = subprocess.run(
            browser_command(web, "native-fixture.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": admin,
                "CUTBACK_FIXTURE_VIEWER": viewer,
                "CUTBACK_NATIVE_FIXTURE_BROWSER": "true",
                "CUTBACK_DEMO_VIDEO": "false",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "fixture-browser"),
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
