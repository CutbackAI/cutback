"""Actual locked upstream producers stay separate from the importer and runtime SDKs."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cutback.evals.importers import prepare_import


def test_actual_pinned_local_cloud_producers_reproduce_the_owned_fixtures(tmp_path, monkeypatch):
    configured = os.getenv("CUTBACK_TEST_CLOUD_PRODUCER_PYTHON")
    if not configured:
        pytest.skip(
            "set CUTBACK_TEST_CLOUD_PRODUCER_PYTHON to the separate locked producer environment"
        )
    root = Path(__file__).resolve().parents[2]
    destination = tmp_path / "owned-synthetic-exports"
    marker = "SYNTHETIC_PROVIDER_KEY_MUST_NOT_REACH_PRODUCERS_OR_FIXTURES"
    monkeypatch.setenv("OPENAI_API_KEY", marker)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", marker)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "examples/cloud-evaluation-imports/generate_fixtures.py"),
            "--producer-python",
            configured,
            "--output",
            str(destination),
        ],
        capture_output=True,
        text=True,
        timeout=210,
    )
    assert result.returncode == 0, "OWNED_PINNED_PRODUCER_REPRODUCTION_FAILED_DIAGNOSTICS_OMITTED"
    assert marker not in result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["provider_calls"] == receipt["external_connection_attempts"] == 0
    assert receipt["cloud_evaluation_service_invoked"] is False
    profiles = {
        "azure-ai-evaluation-1.18.10-results.json": "azure-ai-evaluation-1.18.10-results.v1",
        "vertexai-1.153.1-metrics.jsonl": "vertexai-1.153.1-metrics-jsonl.v1",
        "vertexai-1.153.1-missing.jsonl": "vertexai-1.153.1-metrics-jsonl.v1",
    }
    assert set(receipt["files"]) == set(profiles)
    for name, profile in profiles.items():
        raw = (destination / name).read_bytes()
        assert marker.encode() not in raw
        assert raw == (root / "tests/fixtures/imports" / name).read_bytes()
        imported = prepare_import(raw, profile)
        assert not imported.rejected and imported.preview()["provider_calls"] == 0
        assert imported.preview()["independent_labels"] is False
    assert "azure.ai.evaluation" not in sys.modules and "vertexai.evaluation" not in sys.modules
