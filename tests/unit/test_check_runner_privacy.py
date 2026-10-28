import runpy
import subprocess
import sys
from pathlib import Path

import pytest


def test_runner_suppresses_fixture_arguments_and_redacts_dsn_credentials(monkeypatch, capsys):
    monkeypatch.setenv(
        "CUTBACK_TEST_DATABASE_URL", "postgresql+psycopg://synthetic:unused@127.0.0.1/test"
    )
    monkeypatch.setattr(sys, "argv", ["check.py", "-q", "--tb=long", "--showlocals"])

    def start(arguments, **options):
        assert arguments[-2:] == ["--tb=short", "--no-showlocals"]

        class Process:
            stdout = iter(
                ["failure: 'postgresql+psycopg://synthetic:synthetic-password@127.0.0.1/test'\n"]
            )

            def wait(self):
                return 2

        return Process()

    monkeypatch.setattr(subprocess, "Popen", start)
    with pytest.raises(SystemExit) as outcome:
        runpy.run_path(
            str(Path(__file__).resolve().parents[2] / "scripts/check.py"), run_name="__main__"
        )
    assert outcome.value.code == 2
    output = capsys.readouterr().out
    assert "synthetic-password" not in output and "<credentials-omitted>" in output
