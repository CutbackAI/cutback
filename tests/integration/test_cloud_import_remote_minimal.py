"""Offline cloud preview from a fresh installed wheel without any cloud/native/server SDK."""

import os
import subprocess
from pathlib import Path

import pytest
from support_remote_agent import remote_environment, remote_interpreter, remote_json

CASES = [
    ("bedrock-automated-model-jsonl.v1", "bedrock-automated-model.jsonl", 1),
    ("azure-ai-evaluation-1.18.10-results.v1", "azure-ai-evaluation-1.18.10-results.json", 2),
    ("vertexai-1.153.1-metrics-jsonl.v1", "vertexai-1.153.1-metrics.jsonl", 2),
    ("vertexai-1.153.1-metrics-jsonl.v1", "vertexai-1.153.1-missing.jsonl", 1),
]


@pytest.mark.parametrize("profile,filename,count", CASES)
def test_http_only_install_can_preview_cloud_exports_without_upload_or_sdk(
    tmp_path, profile, filename, count
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to a fresh matching core-plus-HTTPX wheel")
    executable, env = remote_interpreter(configured), remote_environment()
    root = Path(__file__).resolve().parents[2]
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util,sys; from pathlib import Path; import cutback; "
            "import cutback.evals.importers,cutback.evals.cloud_imports; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','azure','vertexai','boto3','fastapi','sqlalchemy','cryptography','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "MATCHING_MINIMAL_CLOUD_IMPORT_WHEEL_REQUIRED"
    result = subprocess.run(
        [
            str(executable),
            "-I",
            "-m",
            "cutback.cli",
            "datasets",
            "preview-import",
            str(root / "tests/fixtures/imports" / filename),
            "--profile",
            profile,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, "MINIMAL_CLOUD_IMPORT_PREVIEW_FAILED_CONTENT_OMITTED"
    summary = remote_json(result)
    assert summary["accepted"] == count and summary["rejected"] == 0
    assert summary["content_uploaded"] is summary["independent_labels"] is False
    assert summary["provider_calls"] == 0 and "Synthetic ticket" not in result.stdout
