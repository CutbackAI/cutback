"""Owned PostgreSQL, encrypted cases, local workers and authenticated responsive UI."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from end_to_end.test_pipeline import server
from support_browser import browser_command

from cutback.demo_semantic_cache import seed_semantic_cache
from cutback.evals.artifacts import EncryptedFileStore
from cutback.sample_app.semantic_cache_fixture import cases
from cutback.server.semantic_upload_worker import run_semantic_upload_one

pytestmark = pytest.mark.postgres


def test_independent_cache_sample_and_authenticated_activation(api, tenant, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_SEMANTIC_CACHE_ENABLED", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    browser = os.getenv("CUTBACK_TEST_BROWSER") == "true"
    receipt = seed_semantic_cache(api[2], api[0], tenant[0], headers=api[1], activate=not browser)
    assert receipt["status"] == "PASSED" and receipt["selected_threshold"] == 0.99
    assert receipt["paid_provider_calls"] == 0 and receipt["realized_savings_usd"] is None
    trials = receipt["report"]["calibration"]["trials"]
    assert trials[0]["assessment"]["false_positive"] > 0
    assert not trials[0]["assessment"]["passed"]
    assert receipt["report"]["confirmation"]["passed"]
    if not browser:
        assert receipt["served_authorized_hit"] and receipt["rejected_unsafe_match"]
        return
    other = (
        api[0].post("/v1/projects", headers=api[1], json={"slug": "semantic-browser-empty"}).json()
    )
    ns = (
        api[0]
        .get(
            "/v1/projects/" + tenant[0] + "/semantic-cache/namespaces/" + receipt["namespace_id"],
            headers=api[1],
        )
        .json()
    )
    upload_file = tmp_path / "owned-controls.json"
    upload_file.write_text(
        json.dumps(
            {
                "workflow": "semantic-cache-controls",
                "environment": "staging",
                "policy_id": ns["specification"]["policy_id"],
                "source_type": "synthetic_fixture",
                "independent_labels": True,
                "provenance_ref": "owned-browser-upload",
                "cases": [case.model_dump(mode="json") for case in cases()[:16]],
            }
        ),
        encoding="utf-8",
    )
    with server(api[2]) as endpoint:
        result = subprocess.run(
            browser_command(Path(__file__).resolve().parents[2] / "web", "semantic-cache.spec.ts"),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                "CUTBACK_SEMANTIC_NAMESPACE": receipt["namespace_id"],
                "CUTBACK_SEMANTIC_QUALIFICATION": receipt["qualification_id"],
                "CUTBACK_SEMANTIC_OTHER_PROJECT": other["id"],
                "CUTBACK_SEMANTIC_UPLOAD_FILE": str(upload_file),
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    # The browser seals content; only the independently invoked worker finalizes it.
    outcome = run_semantic_upload_one(
        api[2].state.sessions, api[2].state.artifact_store, execution_enabled=True
    )
    assert outcome["status"] == "FINALIZED", outcome
