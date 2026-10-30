"""Separate broker/native SQL plus authenticated security/utility dashboard acceptance."""

import os
import subprocess
from pathlib import Path

import pytest
from end_to_end.test_pipeline import server
from support_browser import browser_command

from cutback.demo_security_suites import seed_security_suite
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("CUTBACK_TEST_TOOL_ISOLATION") != "true",
        reason="explicit owned actual tool container acceptance",
    ),
]


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_security_suite_real_tools_native_controls_and_authenticated_browser(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-parent-not-for-tool-service")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    receipt = seed_security_suite(
        api[2], api[0], tenant[0], os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"], headers=api[1]
    )
    assert receipt["sessions"] == 4 and receipt["cleanup_confirmed"]
    assert receipt["paid_provider_calls"] == 0
    assert all(item["attack_success"]["value"] == 0 for item in receipt["security_evaluation"])
    assert all(item["gate"] == "insufficient_evidence" for item in receipt["security_evaluation"])
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        return
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-security-browser-other"})
        .json()
    )
    with server(api[2]) as endpoint:
        result = subprocess.run(
            browser_command(Path(__file__).resolve().parents[2] / "web", "security-suites.spec.ts"),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                "CUTBACK_SECURITY_RUN": receipt["run_id"],
                "CUTBACK_SECURITY_DATASET": receipt["dataset_id"],
                "CUTBACK_SECURITY_PRINCIPAL": receipt["principal_id"],
                "CUTBACK_SECURITY_BROKER": receipt["broker_id"],
                "CUTBACK_SECURITY_OTHER_PROJECT": other["id"],
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
