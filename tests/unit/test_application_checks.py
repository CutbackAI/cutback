import json
import shutil
import subprocess
import time

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from cutback.application_checks import (
    ApplicationCommand,
    execute_command,
    junit,
    prepare,
    run_checks,
    verify,
)
from cutback.cli import app


def repository(path, text):
    path.mkdir()
    (path / "sample.py").write_text(text, encoding="utf-8")
    for args in (
        ("init",),
        ("add", "sample.py"),
        (
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Owned synthetic fixture",
        ),
    ):
        result = subprocess.run(
            [shutil.which("git"), "-C", str(path), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0
    return path


def test_portable_checks_bind_both_trees_and_keep_secret_output_out_of_receipts(
    tmp_path, monkeypatch
):
    base = repository(tmp_path / "base", "print('A')\n")
    proposed = repository(tmp_path / "proposed", "print('B')\n")
    monkeypatch.setenv("CUTBACK_CONTROL_TOKEN", "PRIVATE_AMBIENT_SECRET")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_PROVIDER_SECRET")
    command = ApplicationCommand(
        key="owned-synthetic",
        arguments=[
            "-c",
            "import os; assert 'CUTBACK_CONTROL_TOKEN' not in os.environ "
            "and 'OPENAI_API_KEY' not in os.environ and 'HOME' not in os.environ; "
            "print('PRIVATE_CHECK_OUTPUT')",
        ],
    )
    contract = prepare(base, proposed, [command])
    assert contract.base.tree_hash != contract.proposed.tree_hash
    with pytest.raises(ValueError, match="EXPLICIT_LOCAL"):
        run_checks(contract, base, proposed)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"] and len(receipt["results"]) == 2
    assert "PRIVATE_" not in json.dumps(receipt) and "PRIVATE_" not in junit(receipt)
    assert verify(contract, receipt, base, proposed)["authenticity"] == "not_independently_attested"
    changed = {**receipt, "all_passed": False}
    with pytest.raises(ValueError, match="RECEIPT_OR_SOURCE"):
        verify(contract, changed, base, proposed)
    (proposed / "sample.py").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="CLEAN_TRACKED"):
        verify(contract, receipt, base, proposed)


@pytest.mark.parametrize("failure", ["output", "timeout", "exit"])
def test_local_execution_limits_and_failures_are_not_passes(tmp_path, failure):
    source = {
        "output": "print('PRIVATE_OUTPUT' * 10000)",
        "timeout": "import time; time.sleep(10)",
        "exit": "raise SystemExit(7)",
    }[failure]
    command = ApplicationCommand(
        key="bounded",
        arguments=["-c", source],
        max_output_bytes=100,
        # Only the timeout case is testing a one-second deadline. Process startup
        # under a loaded clean-host matrix must not relabel exit/output fixtures.
        timeout_seconds=1 if failure == "timeout" else 10,
    )
    started = time.monotonic()
    result = execute_command(tmp_path, command)
    assert (
        result["status"]
        == {"output": "output_limit", "timeout": "timeout", "exit": "failed"}[failure]
    )
    assert time.monotonic() - started < command.timeout_seconds + 4
    assert "PRIVATE_OUTPUT" not in json.dumps(result)


def test_descendants_are_reaped_even_when_direct_process_exits(tmp_path):
    marker = tmp_path / "should-not-exist"
    child = (
        "import pathlib,time; time.sleep(1); pathlib.Path("
        + repr(str(marker))
        + ").write_text('escaped')"
    )
    parent = (
        "import subprocess,sys; subprocess.Popen([sys.executable, '-c', "
        + repr(child)
        + "]); print('done')"
    )
    result = execute_command(
        tmp_path, ApplicationCommand(key="descendants", arguments=["-c", parent])
    )
    time.sleep(1.1)
    assert result["status"] == "passed" and not marker.exists()


def test_fixture_environment_and_working_directory_cannot_expand_runtime_scope(tmp_path):
    for env in ({"PATH": "elsewhere"}, {"CUTBACK_FIXTURE_API_KEY": "secret"}, {"PYTHONPATH": "."}):
        with pytest.raises(ValidationError, match="ENVIRONMENT_NOT_PERMITTED"):
            ApplicationCommand(key="unsafe", arguments=["sample.py"], environment=env)
    with pytest.raises(ValidationError):
        ApplicationCommand(key="unsafe", arguments=["sample.py"], working_directory="../outside")
    result = CliRunner().invoke(
        app,
        [
            "checks",
            "run",
            str(tmp_path / "no-contract"),
            str(tmp_path),
            str(tmp_path),
            "--output",
            str(tmp_path / "out.json"),
            "--junit",
            str(tmp_path / "out.xml"),
        ],
    )
    assert result.exit_code == 2 and "EXPLICIT_LOCAL_EXECUTION_REQUIRED" in result.stdout
    assert not (tmp_path / "out.json").exists()


def test_preparation_never_executes_git_clean_filters(tmp_path):
    base = repository(tmp_path / "base", "print('A')\n")
    proposed = repository(tmp_path / "proposed", "print('B')\n")
    marker = tmp_path / "filter-must-not-run"
    for root in (base, proposed):
        (root / ".gitattributes").write_text("*.py filter=cutback-test\n", encoding="utf-8")
        for args in (
            ("add", ".gitattributes"),
            (
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=synthetic@example.invalid",
                "commit",
                "-m",
                "Declare test filter",
            ),
            ("config", "filter.cutback-test.clean", "echo FILTER_EXECUTED > " + str(marker)),
        ):
            result = subprocess.run(
                [shutil.which("git"), "-C", str(root), *args], capture_output=True, timeout=10
            )
            assert result.returncode == 0
    contract = prepare(base, proposed, [ApplicationCommand(key="owned", arguments=["app.py"])])
    assert contract.base.files and not marker.exists()


def test_mutating_check_emits_failed_receipt_and_stops_remaining_execution(tmp_path):
    base = repository(tmp_path / "base", "print('A')\n")
    proposed = repository(tmp_path / "proposed", "print('B')\n")
    command = ApplicationCommand(
        key="mutating-owned-fixture",
        arguments=["-c", "from pathlib import Path; Path('sample.py').write_text('changed')"],
    )
    contract = prepare(base, proposed, [command])
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert not receipt["all_passed"] and len(receipt["results"]) == 1
    assert receipt["results"][0]["status"] == "source_changed"
    assert (proposed / "sample.py").read_text(encoding="utf-8") == "print('B')\n"
    assert 'message="source_changed"' in junit(receipt)


def test_node_command_uses_declared_runtime_without_inherited_credentials(tmp_path, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("optional Node runtime is not installed")
    monkeypatch.setenv("OPENAI_API_KEY", "PRIVATE_PROVIDER_SECRET")
    command = ApplicationCommand(
        key="owned-node-check",
        runtime="node",
        arguments=[
            "-e",
            "if(process.env.OPENAI_API_KEY)process.exit(9);process.stdout.write('PRIVATE_CHECK_OUTPUT')",
        ],
    )
    result = execute_command(tmp_path, command)
    assert result["status"] == "passed" and result["output_bytes_observed"] > 0
    assert "PRIVATE_CHECK_OUTPUT" not in json.dumps(result)
