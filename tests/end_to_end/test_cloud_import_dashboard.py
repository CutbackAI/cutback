"""Actual authenticated browser, encrypted HTTP import and PostgreSQL; no inference."""

import os
import subprocess
from pathlib import Path

import pytest
from support_browser import browser_command
from support_http import owned_collector

from cutback.evals.artifacts import EncryptedFileStore


@pytest.mark.postgres
def test_cloud_import_purpose_privacy_original_values_mobile_and_accessibility(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated cloud import acceptance")
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    token = api[1]["Authorization"][7:]
    web = Path(__file__).resolve().parents[2] / "web"
    with owned_collector(api[2]) as endpoint:
        result = subprocess.run(
            browser_command(web, "imports.spec.ts", "cloud-imports.spec.ts"),
            cwd=web,
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": token,
                "CUTBACK_DEMO_VIDEO": "false",
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "private-browser-output"),
            },
            capture_output=True,
            text=True,
            timeout=240,
        )
    assert result.returncode == 0, (result.stdout + result.stderr).replace(token, "<omitted>")
