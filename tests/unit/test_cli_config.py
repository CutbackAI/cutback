import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from cutback.cli import app
from cutback.core.config import validate_endpoint

runner = CliRunner()


def test_import_offline_no_optional_modules():
    script = (
        "import socket,sys\n"
        'def forbidden(*a, **k):\n    raise RuntimeError("network forbidden")\n'
        "socket.create_connection=forbidden\n"
        "import cutback\n"
        'assert not any(x in sys.modules for x in ("openai","httpx","sqlalchemy","fastapi"))'
    )
    assert subprocess.run([sys.executable, "-c", script], capture_output=True).returncode == 0


def test_cli_help_doctor_and_scan(tmp_path):
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).stdout.strip() == "0.1.0.dev0"
    doctor = runner.invoke(app, ["doctor", "--json"])
    assert doctor.exit_code == 0
    assert json.loads(doctor.stdout)["network_probe"] == "not_performed"
    result = runner.invoke(app, ["scan", str(tmp_path), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["summary"]["actual_spend_usd"] is None


def test_init_and_instrument_diff_does_not_apply(tmp_path):
    assert runner.invoke(app, ["init", "--target", str(tmp_path)]).exit_code == 0
    assert runner.invoke(app, ["init", "--target", str(tmp_path)]).exit_code == 2
    path = tmp_path / "main.py"
    original = '"""module docstring"""\nfrom __future__ import annotations\nprint("hello")\n'
    path.write_text(original)
    result = runner.invoke(app, ["instrument", "--plan", "--target", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.exception
    assert path.read_text() == original
    assert "cutback:bootstrap" in json.loads(result.stdout)["diff"]


@pytest.mark.parametrize(
    "endpoint",
    ["http://example.com", "https://key:secret@example.com", "https://example.com/?key=secret"],
)
def test_endpoint_security(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


def test_collector_setup_failure_never_prints_credentials(monkeypatch):
    monkeypatch.setenv("CUTBACK_DATABASE_URL", "invalid://PRIVATE-DB-PASSWORD")
    monkeypatch.setenv("CUTBACK_ADMIN_TOKEN", "PRIVATE-ADMIN-TOKEN-" + "a" * 40)
    result = runner.invoke(app, ["collector", "serve"])
    assert result.exit_code == 2
    assert "PRIVATE" not in result.stdout
    assert "COLLECTOR_CONFIGURATION_INVALID" in result.stdout
