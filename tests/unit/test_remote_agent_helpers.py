"""Real venv launch and private diagnostics, including Linux's symlink layout."""

import json
import os
import subprocess
import sys

import pytest
from support_remote_agent import remote_environment, remote_interpreter, remote_json


def test_remote_process_uses_the_virtual_environment_prefix(tmp_path):
    location = tmp_path / "independent-venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(location)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert created.returncode == 0, "OWNED_VENV_CREATION_FAILED"
    path = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    child = subprocess.run(
        [
            str(remote_interpreter(path)),
            "-c",
            "import json,sys;print(json.dumps({'prefix':sys.prefix}))",
        ],
        capture_output=True,
        text=True,
        env=remote_environment(),
        timeout=30,
    )
    assert child.returncode == 0, "OWNED_VENV_PREFIX_PROBE_FAILED"
    assert json.loads(child.stdout)["prefix"] == str(location)


@pytest.mark.parametrize("stdout", ["", "synthetic-private-stdout", "[]"])
def test_failed_json_diagnostic_never_includes_child_content_or_arguments(stdout):
    child = subprocess.CompletedProcess(
        ["synthetic-private-argument"],
        1,
        stdout,
        "ModuleNotFoundError: synthetic-private-stderr",
    )
    with pytest.raises(pytest.fail.Exception) as error:
        remote_json(child)
    message = str(error.value)
    assert "REMOTE_AGENT_JSON_PROTOCOL_FAILED" in message
    assert "ModuleNotFoundError" in message and '"exit_code": 1' in message
    assert "synthetic-private" not in message


def test_independent_client_inherits_only_explicit_system_environment(monkeypatch):
    for name in (
        "GITHUB_TOKEN",
        "OPENAI_API_KEY",
        "CUTBACK_CONTROL_TOKEN",
        "COVERAGE_PROCESS_START",
        "PYTHONPATH",
        "PYTHONHOME",
        "_CUTBACK_COMPLETE",
    ):
        monkeypatch.setenv(name, "synthetic-private-sentinel")
    env = remote_environment()
    assert "synthetic-private-sentinel" not in env.values()
    assert env["PYTHONNOUSERSITE"] == env["PYTHONUTF8"] == "1"
