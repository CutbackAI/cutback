"""Independent source comparisons: no application import while choosing checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cutback.application_check_discovery import discover
from cutback.application_checks import ApplicationCheckContract, git, run_checks, verify
from cutback.cli import app

CONFIG = """[tool.cutback.application_checks]
commands = [
  {key='first-full', arguments=['check_application.py', 'first', 'full']},
  {key='first-guard', arguments=['check_application.py', 'first', 'guard']},
  {key='second-full', arguments=['check_application.py', 'second', 'full']},
  {key='second-guard', arguments=['check_application.py', 'second', 'guard']},
  {key='health-full', arguments=['check_application.py', 'health', 'full']},
  {key='health-guard', arguments=['check_application.py', 'health', 'guard']},
  {key='global', arguments=['check_application.py', 'health', 'guard']},
]
[tool.cutback.workflow_checks]
version = 1
[[tool.cutback.workflow_checks.workflows]]
key = 'first'
entrypoints = ['first.py']
resources = ['prompt.txt']
commands = ['first-full', 'first-guard']
regression_commands = ['first-guard']
[[tool.cutback.workflow_checks.workflows]]
key = 'second'
entrypoints = ['second.py']
resources = ['prompt.txt']
commands = ['second-full', 'second-guard']
regression_commands = ['second-guard']
[[tool.cutback.workflow_checks.workflows]]
key = 'health'
entrypoints = ['health.py']
commands = ['health-full', 'health-guard']
regression_commands = ['health-guard']
"""


def files():
    return {
        "pyproject.toml": CONFIG,
        "first.py": "from shared import classify\n",
        "second.py": "import shared\ndef label():\n    return shared.classify('x')\n",
        "shared.py": "def classify(value):\n    return 'A'\n",
        "health.py": "def status():\n    return 'ready'\n",
        "prompt.txt": "Independent synthetic classification prompt.\n",
        "check_application.py": (
            "import sys\n"
            "def check():\n"
            "    if sys.argv[1] == 'first':\n"
            "        from first import classify\n"
            "        assert classify('x') == 'A'\n"
            "    elif sys.argv[1] == 'second':\n"
            "        from second import label\n"
            "        assert label() == 'A'\n"
            "    else:\n"
            "        from health import status\n"
            "        assert status() == 'ready'\n"
            "if __name__ == '__main__':\n    check()\n"
        ),
    }


def repository(path, values):
    path.mkdir()
    for name, value in values.items():
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
        "Independent workflow impact fixture",
    )
    return path


def pair(tmp_path, updates, original=None):
    before = files() if original is None else original
    return (
        repository(tmp_path / "base", before),
        repository(tmp_path / "proposed", {**before, **updates}),
    )


def test_shared_change_selects_both_consumers_preserves_unchanged_guard_and_global(tmp_path):
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"].replace("'A'", "'B'")})
    report = discover(base, proposed, select_affected=True)
    plan = report["impact"]
    assert plan["mode"] == "affected_with_regression_guards"
    assert plan["changed_paths"] == ["shared.py"]
    assert [(w["key"], w["status"]) for w in plan["workflows"]] == [
        ("first", "affected"),
        ("second", "affected"),
        ("health", "unchanged_declared_scope"),
    ]
    assert plan["omitted_commands"] == ["health-full"]
    assert plan["global_commands"] == ["global"]
    assert plan["selected_commands"][-1] == "health-guard"
    assert not plan["application_imported"] and plan["provider_calls_authorized"] == 0
    contract = ApplicationCheckContract.model_validate(report["contract"])
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert not receipt["all_passed"]
    assert len(receipt["results"]) == 12
    assert all(r["status"] == "passed" for r in receipt["results"] if r["phase"] == "base")
    failed = {r["command"] for r in receipt["results"] if r["status"] == "failed"}
    assert failed == {"first-full", "first-guard", "second-full", "second-guard"}
    assert "Independent synthetic classification prompt" not in json.dumps(report)


def test_default_retains_every_command_and_cli_selective_is_explicit(tmp_path):
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# harmless change\n"})
    default = discover(base, proposed)
    assert default["impact"]["mode"] == "all_commands_affected_first"
    assert len(default["contract"]["commands"]) == 7
    output, report = tmp_path / "contract.json", tmp_path / "discovery.json"
    cli = CliRunner().invoke(
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
            "--select-affected",
        ],
    )
    assert cli.exit_code == 0, cli.exception
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["impact"]["omitted_commands"] == ["health-full"]
    assert json.loads(output.read_text(encoding="utf-8")) == data["contract"]
    contract = ApplicationCheckContract.model_validate(data["contract"])
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"]
    assert verify(contract, receipt, base, proposed)


def test_resource_changes_affect_both_declared_consumers(tmp_path):
    base, proposed = pair(tmp_path, {"prompt.txt": "Different synthetic instructions.\n"})
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert [w["key"] for w in plan["workflows"] if w["status"] == "affected"] == ["first", "second"]
    assert plan["omitted_commands"] == ["health-full"]


@pytest.mark.parametrize("value", ["true", 1, None])
def test_selective_flag_is_an_explicit_boolean_before_source_inspection(tmp_path, value):
    with pytest.raises(ValueError, match="EXPLICIT_BOOLEAN_SELECTION_REQUIRED"):
        discover(tmp_path / "absent-base", tmp_path / "absent-proposed", select_affected=value)


@pytest.mark.parametrize(
    "source",
    [
        "import importlib as loader\nloader.import_module('shared')\n",
        "from importlib import import_module as load\nload('shared')\n",
        "__import__('shared')\n",
        "loader = __import__\nloader('shared')\n",
        "runner = eval\nrunner('1 + 1')\n",
        "runner = exec\nrunner('x = 1')\n",
        "import builtins as native\nrunner = native.eval\nrunner('1 + 1')\n",
        "loader = getattr(__builtins__, '__import__')\nloader('shared')\n",
        "space = globals()\nspace['loader']('shared')\n",
        "from builtins import __import__ as load\nload('shared')\n",
        "import sys as runtime\nruntime.path.insert(0, 'lib')\n",
        "from sys import path as search\nsearch.insert(0, 'lib')\n",
        "__path__ = ['synthetic']\n",
        "exec('x = 1')\n",
        "from shared import *\n",
        "def broken(:\n",
    ],
)
def test_opaque_or_invalid_python_falls_back_without_execution(tmp_path, source):
    original = {**files(), "opaque.py": source}
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"}, original)
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert plan["mode"] == "all_commands_fallback"
    assert plan["fallback_reasons"]
    assert len(plan["selected_commands"]) == 7 and not plan["omitted_commands"]


def test_unknown_changed_file_and_absent_manifest_never_omit_checks(tmp_path):
    base, proposed = pair(tmp_path, {"other-settings.json": '{"mode": "synthetic"}'})
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert "changed_paths_outside_declared_workflows" in plan["fallback_reasons"]
    assert not plan["omitted_commands"]
    no_manifest = {"test_example.py": "raise RuntimeError('must not collect')\n"}
    a = repository(tmp_path / "no-base", no_manifest)
    b = repository(tmp_path / "no-proposed", no_manifest)
    fallback = discover(a, b, select_affected=True)["impact"]
    assert fallback["fallback_reasons"] == ["no_reviewed_workflow_declaration"]
    assert len(fallback["selected_commands"]) == 1


def test_aliased_builtin_import_refuses_selective_omission(tmp_path):
    original = {**files(), "opaque.py": "loader = __import__\nloader('shared')\n"}
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"}, original)
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert plan["mode"] == "all_commands_fallback"
    assert "dynamic_python_execution_or_import" in plan["fallback_reasons"]
    assert len(plan["selected_commands"]) == 7 and not plan["omitted_commands"]


def test_transitive_relative_imports_and_package_initializers_are_included(tmp_path):
    original = files()
    original["first.py"] = "from pkg.consumer import classify\n"
    original["second.py"] = "from pkg import consumer\n"
    original.update(
        {
            "src/pkg/__init__.py": "from .shared import classify\n",
            "src/pkg/consumer.py": "from .shared import classify\n",
            "src/pkg/shared.py": "from shared import classify\n",
        }
    )
    base, proposed = pair(
        tmp_path, {"src/pkg/shared.py": "from shared import classify\n# change\n"}, original
    )
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert not plan["fallback_reasons"]
    for workflow in plan["workflows"][:2]:
        assert workflow["status"] == "affected"
        assert "src/pkg/__init__.py" in workflow["dependency_paths"]
        assert "shared.py" in workflow["dependency_paths"]


def test_ambiguous_root_and_src_module_names_force_all_commands(tmp_path):
    original = {**files(), "src/shared.py": "value = 1\n"}
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"}, original)
    assert (
        "ambiguous_python_module"
        in discover(base, proposed, select_affected=True)["impact"]["fallback_reasons"]
    )


@pytest.mark.parametrize(
    "replacement",
    [
        CONFIG.replace("version = 1", "version = true"),
        CONFIG.replace("version = 1", "version = 2"),
        CONFIG.replace("entrypoints = ['first.py']", "entrypoints = ['../outside.py']"),
        CONFIG.replace("entrypoints = ['first.py']", "entrypoints = ['missing.py']"),
        CONFIG.replace("entrypoints = ['first.py']", "entrypoints = ['prompt.txt']"),
        CONFIG.replace("regression_commands = ['first-guard']", "regression_commands = []"),
        CONFIG.replace(
            "regression_commands = ['first-guard']", "regression_commands = ['health-guard']"
        ),
        CONFIG.replace(
            "commands = ['first-full', 'first-guard']", "commands = ['absent', 'first-guard']"
        ),
        CONFIG.replace("key = 'second'", "key = 'first'"),
        CONFIG.replace("resources = ['prompt.txt']", "resources = ['prompt.txt', 'prompt.txt']"),
    ],
    ids=[
        "wrong-version-type",
        "unsupported-version",
        "unsafe-path",
        "missing-path",
        "non-python-entrypoint",
        "empty-regression-guards",
        "foreign-regression-command",
        "unknown-command",
        "duplicate-workflow",
        "duplicate-path",
    ],
)
def test_invalid_declarations_cannot_silently_reduce_checks(tmp_path, replacement):
    original = {**files(), "pyproject.toml": replacement}
    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"}, original)
    with pytest.raises(ValueError):
        discover(base, proposed, select_affected=True)


def test_changed_declaration_and_changed_test_definitions_are_refused(tmp_path):
    base, proposed = pair(
        tmp_path, {"pyproject.toml": CONFIG.replace("version = 1", "version = 2")}
    )
    with pytest.raises(ValueError, match="RUNNER_CHANGED"):
        discover(base, proposed, select_affected=True)


def test_no_changes_runs_every_check_and_application_top_level_is_not_imported(tmp_path):
    original = {**files(), "first.py": "raise RuntimeError('never import during planning')\n"}
    base, proposed = pair(tmp_path, {}, original)
    report = discover(base, proposed, select_affected=True)
    assert report["impact"]["fallback_reasons"] == ["no_source_changes"]
    assert len(report["contract"]["commands"]) == 7
    assert "never import during planning" not in json.dumps(report)


@pytest.mark.parametrize(
    "bound,reason",
    [
        ("MAX_PYTHON_BYTES", "python_analysis_byte_bound_exceeded"),
        ("MAX_SYNTAX_NODES", "python_analysis_node_bound_exceeded"),
        ("MAX_IMPORT_EDGES", "python_analysis_edge_bound_exceeded"),
        ("MAX_WORKFLOW_PATHS", "workflow_dependency_report_bound_exceeded"),
    ],
)
def test_analysis_bounds_keep_every_check_and_expose_truncation(
    tmp_path, monkeypatch, bound, reason
):
    import cutback.application_check_impact as impact

    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"})
    monkeypatch.setattr(impact, bound, 1)
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert reason in plan["fallback_reasons"]
    assert len(plan["selected_commands"]) == 7 and not plan["omitted_commands"]
    if bound == "MAX_WORKFLOW_PATHS":
        assert any(w["dependency_paths_truncated"] for w in plan["workflows"])
        assert all(len(w["dependency_paths"]) <= 1 for w in plan["workflows"])


def test_source_changed_during_planning_refuses_contract(tmp_path, monkeypatch):
    import cutback.application_check_impact as impact

    base, proposed = pair(tmp_path, {"shared.py": files()["shared.py"] + "# change\n"})
    original = impact.source_graph

    def changed(root, tree):
        result = original(root, tree)
        if root == base:
            (base / "shared.py").write_text("changed = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(impact, "source_graph", changed)
    with pytest.raises(ValueError, match="CLEAN_TRACKED|SOURCE_CHANGED"):
        discover(base, proposed, select_affected=True)


def test_both_graphs_include_new_dependencies_and_report_is_deterministic(tmp_path):
    original = {**files(), "later.py": "value = 1\n"}
    base, proposed = pair(
        tmp_path,
        {
            "first.py": "from later import value\n",
            "later.py": "value = 2\n",
        },
        original,
    )
    report = discover(base, proposed, select_affected=True)
    assert report == discover(base, proposed, select_affected=True)
    first = report["impact"]["workflows"][0]
    assert first["changed_dependencies"] == ["first.py", "later.py"]
    assert "shared.py" in first["dependency_paths"] and "later.py" in first["dependency_paths"]


def test_entrypoint_without_imports_still_depends_on_its_package_initializer(tmp_path):
    original = files()
    original["pyproject.toml"] = CONFIG.replace(
        "entrypoints = ['first.py']", "entrypoints = ['src/pkg/first.py']"
    )
    original["src/pkg/first.py"] = "value = 1\n"
    original["src/pkg/__init__.py"] = "shared = 1\n"
    base, proposed = pair(tmp_path, {"src/pkg/__init__.py": "shared = 2\n"}, original)
    plan = discover(base, proposed, select_affected=True)["impact"]
    assert not plan["fallback_reasons"]
    assert plan["workflows"][0]["status"] == "affected"
    assert plan["workflows"][0]["changed_dependencies"] == ["src/pkg/__init__.py"]


@pytest.mark.parametrize("scenario", ["good", "broken", "broken-resource"])
def test_standalone_example_uses_real_both_tree_checks_and_rejects_broken_change(
    tmp_path, scenario
):
    broken = scenario != "good"
    sample = Path(__file__).resolve().parents[2] / "examples/workflow-checks/walkthrough.py"
    workspace = tmp_path / "independent-example"
    result = subprocess.run(
        [sys.executable, str(sample), "--workspace", str(workspace), "--execute-local"]
        + (["--" + scenario] if broken else []),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == (1 if broken else 0), result.stderr
    summary = json.loads(result.stdout)
    assert summary["all_passed"] is not broken
    assert summary["omitted_commands"] == ["health-full"]
    assert len(summary["results"]) == 10
    if broken:
        assert {row["command"] for row in summary["results"] if row["status"] == "failed"} == {
            "triage-full",
            "triage-guard",
            "escalation-full",
            "escalation-guard",
        }
    report = json.loads((workspace / "discovery.json").read_text(encoding="utf-8"))
    assert report["impact"]["changed_paths"] == [
        "prompt.txt" if scenario == "broken-resource" else "shared.py"
    ]
