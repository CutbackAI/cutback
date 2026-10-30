"""Separate checker/broker processes, actual contained SQL and guarded-suite browser."""

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
        reason="explicit owned Docker guarded-security acceptance",
    ),
]


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


@pytest.mark.parametrize("deny_legitimate", [False, True])
def test_actual_guarded_security_suite_preserves_policy_outcome_check_coverage_and_utility(
    api, tenant, tmp_path, monkeypatch, deny_legitimate
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    receipt = seed_security_suite(
        api[2],
        api[0],
        tenant[0],
        os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"],
        headers=api[1],
        guarded=True,
        deny_legitimate=deny_legitimate,
    )
    assert receipt["sessions"] == 4 and receipt["cleanup_confirmed"]
    assert receipt["paid_provider_calls"] == 0
    assert receipt["runtime_guardrails"]["checks"] == 14
    assert receipt["runtime_guardrails"]["withheld_operations"] == (3 if deny_legitimate else 0)
    assert receipt["runtime_guardrails"]["state_changes"] == 3
    for report in receipt["security_evaluation"]:
        assert report["runtime_guardrails"]["policy_decisions_known"]
        assert report["runtime_guardrails"]["coverage_complete"] is (not deny_legitimate)
        assert report["runtime_guardrails"]["benign_family_interruption"]["value"] == int(
            deny_legitimate
        )
        assert report["guardrail_false_positive_rate"] is None
        assert report["gate"] == "insufficient_evidence"
    if not deny_legitimate or os.getenv("CUTBACK_TEST_BROWSER") != "true":
        return
    with server(api[2]) as endpoint:
        completed = subprocess.run(
            browser_command(
                Path(__file__).resolve().parents[2] / "web", "guarded-security.spec.ts"
            ),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                "CUTBACK_UI_VIEWER": tenant[2]["Authorization"][7:],
                "CUTBACK_GUARDED_SECURITY_RUN": receipt["run_id"],
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    output = completed.stdout + completed.stderr
    for credential in (api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]):
        output = output.replace(credential, "<credential-omitted>")
    assert completed.returncode == 0, output
