"""Container preparation needs core only, without Docker access or optional packages."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from support_remote_agent import remote_environment, remote_interpreter


def test_minimal_core_client_prepares_container_contract_without_executing_anything(tmp_path):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to the matching core-only installed wheel")
    executable, env = remote_interpreter(configured), remote_environment()
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/application-check-v1-compatibility.json").read_text()
    )
    source, destination = tmp_path / "v1.json", tmp_path / "v2.json"
    source.write_text(json.dumps(fixture["contract"]))
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util; from cutback.application_container import isolate; "
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','fastapi','sqlalchemy','cryptography','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "APPLICATION_CONTAINER_REQUIRES_MATCHING_CORE_WHEEL"
    completed = subprocess.run(
        [
            str(executable),
            "-I",
            "-m",
            "cutback.cli",
            "checks",
            "isolate",
            str(source),
            "--image-id",
            "sha256:" + "a" * 64,
            "--output",
            str(destination),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, "CORE_CONTAINER_PREPARATION_FAILED"
    value = json.loads(destination.read_text())
    assert value["schema_version"] == "cutback.application-check.v2"
    assert value["base"] == fixture["contract"]["base"]
    assert value["commands"] == fixture["contract"]["commands"]
    assert value["container"]["image_id"] == "sha256:" + "a" * 64
