"""Execute a real oversized fixture without putting its code or secret in argv."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


def fixture_module(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "owned_image_execution_pipe", scripts / "image_acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_large_fixture_executes_with_short_argv_and_anonymous_private_input(monkeypatch):
    module = fixture_module(monkeypatch)
    code = (
        "#"
        + "x" * 70000
        + (
            "\nassert os.environ['OWNED_PIPE_SECRET']=='synthetic-private-credential'\n"
            "print('OWNED_LARGE_FIXTURE_EXECUTED')\n"
        )
    )
    calls = []

    def checked(command, *, source, timeout):
        assert len(subprocess.list2cmdline(command)) < 1000
        assert code not in " ".join(command) and "synthetic-private-credential" not in " ".join(
            command
        )
        payload = json.loads(source)
        assert (
            payload["code"] == code
            and payload["environment"]["OWNED_PIPE_SECRET"] == "synthetic-private-credential"
        )
        # Exercise the identical fixed trampoline in a real Python process;
        # the Docker transport itself is covered by the actual image gate.
        result = subprocess.run(
            [sys.executable, *command[-2:]],
            input=source,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        assert result.returncode == 0 and result.stderr == ""
        calls.append(True)
        return result.stdout

    monkeypatch.setattr(module, "checked", checked)
    result = module.inside(
        "owned-fixture-container",
        code,
        {"OWNED_PIPE_SECRET": "synthetic-private-credential"},
        timeout=10,
    )
    assert calls == [True] and result == "OWNED_LARGE_FIXTURE_EXECUTED\n"


def test_oversized_fixture_pipe_payload_refused_before_process_creation(monkeypatch):
    module = fixture_module(monkeypatch)
    monkeypatch.setattr(
        module, "checked", lambda *args, **kwargs: pytest.fail("UNBOUNDED_CHILD_STARTED")
    )
    with pytest.raises(ValueError, match="IMAGE_FIXTURE_PIPE_PAYLOAD_TOO_LARGE"):
        module.inside("owned-fixture-container", "x" * 1048577, {})
