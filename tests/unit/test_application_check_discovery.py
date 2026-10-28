"""Check discovery is local source inspection, not test collection or application import."""

import json

import pytest
from typer.testing import CliRunner

from cutback.application_check_discovery import discover
from cutback.application_checks import (
    ApplicationCheckContract,
    ApplicationCommand,
    git,
    prepare,
    run_checks,
)
from cutback.cli import app


def repository(path, files):
    path.mkdir()
    for name, value in files.items():
        destination = path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(value, encoding="utf-8")
    git(path, "init")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent check-discovery fixture",
    )
    return path


def test_guarded_script_discovery_and_real_checks_reject_a_broken_shared_consumer(tmp_path):
    files = {
        "shared.py": "def classify(value):\n    return 'A'\n",
        "check_application.py": (
            "from shared import classify\n"
            "def check():\n    assert classify('owned') == 'A'\n"
            "if __name__ == '__main__':\n    check()\n"
        ),
    }
    base = repository(tmp_path / "base", files)
    proposed = repository(
        tmp_path / "proposed", {**files, "shared.py": files["shared.py"].replace("'A'", "'B'")}
    )
    report = discover(base, proposed)
    assert report["application_commands_executed"] == 0
    assert report["provider_calls_authorized"] == 0
    contract = report["contract"]
    assert contract["commands"][0]["arguments"] == ["check_application.py"]
    result = run_checks(
        ApplicationCheckContract.model_validate(contract), base, proposed, execute_local=True
    )
    assert not result["all_passed"]
    assert [row["status"] for row in result["results"]] == ["passed", "failed"]


def test_pytest_discovery_never_collects_or_imports_and_cli_needs_no_handwritten_commands(tmp_path):
    files = {
        "test_application.py": "raise RuntimeError('collection is forbidden during discovery')\n",
        "conftest.py": "raise RuntimeError('fixture import is forbidden during discovery')\n",
        "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['.']\n",
    }
    base, proposed = [repository(tmp_path / label, files) for label in ("base", "proposed")]
    output, report = tmp_path / "checks.json", tmp_path / "discovery.json"
    result = CliRunner().invoke(
        app,
        [
            "checks",
            "discover",
            str(base),
            str(proposed),
            "--output",
            str(output),
            "--report",
            str(report),
        ],
    )
    assert result.exit_code == 0, result.exception
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["commands"][0]["arguments"] == ["-m", "pytest"]
    evidence = json.loads(report.read_text(encoding="utf-8"))
    assert not evidence["application_imported"]
    assert "workflow_test_coverage_not_independently_established" in evidence["unknowns"]
    assert "RuntimeError" not in report.read_text(encoding="utf-8")


def test_explicit_project_commands_are_bounded_and_identical_in_both_trees(tmp_path):
    config = """[tool.cutback.application_checks]
commands = [{key='owned-syntax', arguments=['-m', 'compileall', '-q', '.']}]
"""
    base = repository(tmp_path / "base", {"pyproject.toml": config, "app.py": "x = 1\n"})
    proposed = repository(tmp_path / "proposed", {"pyproject.toml": config, "app.py": "x = 2\n"})
    report = discover(base, proposed)
    assert report["contract"]["commands"][0]["key"] == "owned-syntax"
    assert report["origins"]["base"]["owned-syntax"] == "tracked_pyproject_explicit_declaration"
    changed = repository(
        tmp_path / "changed", {"pyproject.toml": config.replace("compileall", "unittest")}
    )
    with pytest.raises(ValueError, match="RUNNER_CHANGED"):
        discover(base, changed)


@pytest.mark.parametrize(
    "script,accepted",
    [
        ("node --test", True),
        ("node --test && echo unsafe", False),
        ("vitest run", False),
        ("$(touch unsafe)", False),
    ],
)
def test_node_discovery_never_interprets_package_shell_scripts(tmp_path, script, accepted):
    files = {"package.json": json.dumps({"scripts": {"test": script}})}
    if accepted:
        files["owned.test.mjs"] = (
            "import test from 'node:test'; import assert from 'node:assert/strict';\n"
            "test('independent owned contract', () => assert.equal(2 + 2, 4));\n"
        )
    base, proposed = [repository(tmp_path / label, files) for label in ("base", "proposed")]
    if accepted:
        report = discover(base, proposed)
        assert report["contract"]["commands"][0]["runtime"] == "node"
        assert report["contract"]["commands"][0]["arguments"] == ["--test"]
        assert run_checks(
            ApplicationCheckContract.model_validate(report["contract"]),
            base,
            proposed,
            execute_local=True,
        )["all_passed"]
    else:
        with pytest.raises(ValueError, match="NO_DISCOVERED_COMMAND"):
            discover(base, proposed)


def test_discovery_rejects_dirty_sources_and_outputs_no_application_content(tmp_path):
    files = {"test_owned.py": "PRIVATE_APPLICATION_BODY = 'not output'\n"}
    base, proposed = [repository(tmp_path / label, files) for label in ("base", "proposed")]
    report = discover(base, proposed)
    assert "PRIVATE_APPLICATION_BODY" not in json.dumps(report)
    (base / "test_owned.py").write_text("changed = True\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CLEAN_TRACKED"):
        discover(base, proposed)


def test_unsupported_application_needs_explicit_commands_instead_of_invented_coverage(tmp_path):
    base, proposed = [
        repository(tmp_path / label, {"app.py": "x = 1\n"}) for label in ("base", "proposed")
    ]
    with pytest.raises(ValueError, match="NO_DISCOVERED_COMMAND"):
        discover(base, proposed)


def test_discovery_and_manual_preparation_reject_symlink_roots_before_resolution(tmp_path):
    files = {"test_owned.py": "x = 1\n"}
    base, proposed = [repository(tmp_path / label, files) for label in ("base", "proposed")]
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(base, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a directory symlink requires host privileges")
    for action in (
        lambda: discover(alias, proposed),
        lambda: prepare(
            alias, proposed, [ApplicationCommand(key="owned", arguments=["-m", "pytest"])]
        ),
    ):
        with pytest.raises(ValueError, match="SYMLINK_ROOT_DENIED"):
            action()


@pytest.mark.parametrize(
    "configuration",
    [
        "[tool]\npytest = 'invalid-table'\n",
        "[tool.cutback.application_checks]\ncommands = []\n",
        "[tool.cutback.application_checks]\n"
        "commands = [{key='owned', arguments=['x.py'], "
        "environment={OPENAI_API_KEY='SYNTHETIC_SECRET_SENTINEL'}}]\n",
    ],
)
def test_invalid_or_credential_bearing_declarations_fail_without_printing_source(
    tmp_path, configuration
):
    files = {"pyproject.toml": configuration, "test_owned.py": "x = 1\n"}
    base, proposed = [repository(tmp_path / label, files) for label in ("base", "proposed")]
    result = CliRunner().invoke(
        app,
        [
            "checks",
            "discover",
            str(base),
            str(proposed),
            "--output",
            str(tmp_path / "contract.json"),
            "--report",
            str(tmp_path / "report.json"),
        ],
    )
    assert result.exit_code == 2
    assert "SYNTHETIC_SECRET_SENTINEL" not in result.stdout
    assert "APPLICATION_CHECK_DISCOVERY_NEEDS_EXPLICIT_REVIEW" in result.stdout
    assert not (tmp_path / "contract.json").exists()
