import os
import subprocess
from pathlib import Path

import httpx
import pytest
from support_browser import browser_command
from test_pipeline import server

from cutback.demo_agents import seed_agent_episodes
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_reactive_native_http_database_and_authenticated_browser(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated reactive user acceptance")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    with (
        server(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], trust_env=False, timeout=30) as control,
    ):
        receipt = seed_agent_episodes(api[2], control, tenant[0], reactive=True)
        assert receipt["paid_provider_calls"] == 0 and receipt["reactive_user_simulation"]
        web = Path(__file__).resolve().parents[2] / "web"
        result = subprocess.run(
            browser_command(web, "reactive-users.spec.ts", "--reporter=line"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_REACTIVE_BROWSER": "true",
                "CUTBACK_REACTIVE_VIEWER": viewer,
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser-output"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode:
            safe = (
                (result.stdout + result.stderr)
                .replace(token, "<credential-omitted>")
                .replace(viewer, "<credential-omitted>")
            )
            pytest.fail(safe, pytrace=False)
