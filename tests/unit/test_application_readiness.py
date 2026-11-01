"""An independent application gets concrete, non-executing first-use decisions."""

import json
import socket

import pytest
from support_agent import fresh_application
from typer.testing import CliRunner

from cutback.agent import analyze, load_session, workspace
from cutback.application_checks import git
from cutback.application_readiness import diagnose, render, summarize, tool_dependencies
from cutback.cli import app
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import write_private


def repository(root):
    git(root, "init")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Owned independent preflight fixture",
    )


def no_network(*args, **kwargs):
    raise AssertionError("OFFLINE_PREFLIGHT_MUST_NOT_CONNECT")


def test_fresh_diagnosis_never_imports_collects_or_reveals_application_content(
    tmp_path, monkeypatch
):
    fresh_application(tmp_path)
    source = (tmp_path / "app.py").read_text()
    original = source + '\nraise RuntimeError("application-import-sentinel")\n'
    (tmp_path / "app.py").write_text(original)
    (tmp_path / "test_owned.py").write_text('raise RuntimeError("collection-sentinel")\n')
    (tmp_path / ".env").write_text("PRIVATE_TOKEN=credential-sentinel\n")
    (tmp_path / ".gitignore").write_text(".cutback/\n.env\n")
    repository(tmp_path)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    result = CliRunner().invoke(app, ["workflow", "diagnose", "--target", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.exception
    report = json.loads(result.stdout)
    assert report["readiness"] == "REVIEW_PREPARATION_INPUTS"
    assert report["source_contracts"]["qualified_request_profiles"] == 1
    assert report["application_checks"]["candidate_commands"] == 1
    assert report["application_checks"]["candidate_runtimes"] == ["python"]
    assert report["application_checks"]["workflow_coverage"] == "unknown"
    assert report["application_checks"]["commands_executed"] == 0
    assert report["next_actions"][0]["argv"] == ["cutback", "workflow", "prepare", "--help"]
    assert report["next_actions"][0]["automatic_execution"] is False
    assert not report["data"]["rights_verified"] and not report["collector"]["contacted"]
    assert report["provider_calls"] == report["application_commands_executed"] == 0
    assert report["startup"]["review_commands"][0]["argv"][-4:] == [
        "--entrypoint",
        "app.py",
        "--plan",
        "--json",
    ]
    assert (tmp_path / "app.py").read_text() == original
    assert all(
        s not in result.stdout
        for s in (
            "application-import-sentinel",
            "collection-sentinel",
            "credential-sentinel",
            "Owned synthetic classification task",
        )
    )
    expected = {k: v for k, v in report.items() if k != "report_sha256"}
    assert report["report_sha256"] == fingerprint(expected)
    text = CliRunner().invoke(app, ["workflow", "diagnose", "--target", str(tmp_path)])
    assert text.exit_code == 0 and "Application checks: CHECK_CANDIDATES_NEED_REVIEW" in text.stdout
    assert "Required decisions:" in text.stdout


def test_rescan_explains_changed_unsupported_request_without_replaying_it(tmp_path):
    fresh_application(tmp_path)
    first = diagnose(tmp_path)
    path = tmp_path / "app.py"
    original = path.read_text().replace("max_output_tokens=64", "**runtime_options")
    path.write_text(original)
    second = diagnose(tmp_path)
    assert first["analysis_sha256"] != second["analysis_sha256"]
    assert second["readiness"] == "SOURCE_ADAPTER_REQUIRED"
    assert (
        second["source_contracts"]["issues"][0]["diagnostic_code"]
        == "EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW"
    )
    assert second["next_actions"][0]["argv"] == ["cutback", "workflow", "explain", "--target", "."]
    assert "runtime_options" not in json.dumps(second) and path.read_text() == original
    assert second["application_checks"]["status"] == "SOURCE_OR_CHECK_DECLARATION_REVIEW_REQUIRED"


def test_existing_charged_intent_and_authority_are_not_replaced(tmp_path):
    fresh_application(tmp_path)
    analyze(tmp_path)
    _, directory = workspace(tmp_path)
    state = load_session(tmp_path)
    state["phase"] = "QUEUED"
    state["plan"] = {"id": "owned-existing-intent", "hash": "a" * 64}
    state["approval"] = {"private": "approval-sentinel"}
    write_private(directory / "session.json", state)
    (tmp_path / "app.py").write_text((tmp_path / "app.py").read_text().replace("64", "80"))
    report = diagnose(tmp_path)
    current = load_session(tmp_path)
    assert report["readiness"] == "CONTINUE_EXISTING_WORKFLOW"
    assert report["source_freshness"] == "saved_active_intent_source_has_changed"
    assert current["id"] == state["id"] and current["plan"] == state["plan"]
    assert current["approval"] == state["approval"] and current["phase"] == "QUEUED"
    assert "approval-sentinel" not in json.dumps(report)
    assert report["next_actions"][0]["argv"] == ["cutback", "workflow", "status", "--target", "."]
    assert not report["automatic_billable_retry"]


def test_multiple_workflows_get_selection_vectors_without_arbitrary_shell_execution(tmp_path):
    fresh_application(tmp_path)
    path = tmp_path / "app.py"
    # A distinct source-level function is a separate workflow, not duplicate evidence.
    path.write_text(
        path.read_text()
        + '\ndef other(text):\n    return client.responses.create(model="gpt-4.1", '
        "input=text, max_output_tokens=64)\n"
    )
    report = diagnose(tmp_path)
    assert report["readiness"] == "SELECT_WORKFLOW_OR_ADAPTER"
    assert len(report["workflow_choices"]) == len(report["next_actions"]) == 2
    for action in report["next_actions"]:
        assert action["argv"][:4] == ["cutback", "optimize", ".", "--workflow"]
        assert not action["automatic_execution"]
    selected = diagnose(tmp_path, workflow=report["workflow_choices"][0]["workflow_id"])
    assert selected["readiness"] == "REVIEW_PREPARATION_INPUTS"
    assert len(selected["workflow_choices"]) == 1


@pytest.mark.parametrize("changed", ["dirty", "malformed", "unknown"])
def test_check_readiness_never_turns_unreadable_or_unknown_checks_into_coverage(tmp_path, changed):
    fresh_application(tmp_path)
    (tmp_path / ".gitignore").write_text(".cutback/\n")
    if changed == "malformed":
        (tmp_path / "pyproject.toml").write_text(
            "[tool.cutback.application_checks\nsecret-sentinel"
        )
    repository(tmp_path)
    if changed == "dirty":
        (tmp_path / "app.py").write_text("raise RuntimeError('changed-sentinel')\n")
    report = diagnose(tmp_path)
    checks = report["application_checks"]
    expected = (
        "NO_CHECK_CANDIDATES"
        if changed == "unknown"
        else "SOURCE_OR_CHECK_DECLARATION_REVIEW_REQUIRED"
    )
    assert checks["status"] == expected and checks["workflow_coverage"] == "unknown"
    assert "sentinel" not in json.dumps(report)
    assert (
        "discard" in checks["next_action"]
        if changed != "unknown"
        else "known bad" in checks["next_action"]
    )


def test_distribution_metadata_is_not_application_sdk_qualification(monkeypatch):
    monkeypatch.setattr(
        "cutback.application_readiness.version", lambda name: "version\nsecret-sentinel"
    )
    result = tool_dependencies()
    assert result["installed_versions"]["openai"] == "unrecognized_version_metadata"
    assert result["application_sdk_versions"] is None
    assert "3.35.1" in result["qualified_observer_versions"]["openai"]
    assert "secret-sentinel" not in json.dumps(result)


def test_fixed_policy_wrapper_keeps_whole_program_preparation_and_external_startup(tmp_path):
    from support_policy_application import application

    root = application(tmp_path)
    report = diagnose(root)
    assert report["readiness"] == "REVIEW_PREPARATION_INPUTS"
    assert report["workflow_choices"][0]["modes"] == ["source_policy_program"]
    assert report["next_actions"][0]["argv"] == ["cutback", "source-policy", "prepare", "--help"]
    assert report["startup"]["fixed_policy_consumers"] == [
        {"source_file": "app.py", "status": "EXTERNAL_PROCESS_STARTUP_REQUIRED"}
    ]
    assert all(action["argv"][5] != "app.py" for action in report["startup"]["review_commands"])
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(report)


def test_explicit_check_declarations_are_counted_without_echoing_or_executing_them(tmp_path):
    fresh_application(tmp_path)
    (tmp_path / ".gitignore").write_text(".cutback/\n")
    (tmp_path / "pyproject.toml").write_text(
        "[tool.cutback.application_checks]\n"
        "commands=[{key='private-command-sentinel', arguments=['-c','private-body-sentinel']}]\n"
    )
    repository(tmp_path)
    report = diagnose(tmp_path)
    assert report["application_checks"]["candidate_commands"] == 1
    assert report["application_checks"]["candidate_origins"] == [
        "tracked_pyproject_explicit_declaration"
    ]
    assert "sentinel" not in json.dumps(report)


def test_report_is_bounded_and_fixed_guidance_cannot_echo_stored_issue_content():
    state = {
        "phase": "ANALYZED",
        "analysis_sha256": "a" * 64,
        "preparation_options": [
            {"workflow_id": f"workflow-{i}", "modes": ["native_search"]} for i in range(23)
        ],
        "unresolved": [
            {"source_file": "owned\nfile.py", "diagnostic_code": "secret-sentinel"}
            for _ in range(22)
        ],
    }
    checks = {"status": "NO_CHECK_CANDIDATES", "next_action": "Declare bounded checks."}
    report = summarize(state, checks=checks, dependencies={})
    assert len(report["workflow_choices"]) == len(report["next_actions"]) == 20
    assert report["omitted_workflow_choices"] == 3
    assert report["source_contracts"]["omitted_issues"] == 2
    assert "secret-sentinel" not in json.dumps(report)
    assert "owned\\nfile.py" in render(report) and "owned\nfile.py" not in render(report)


def test_legacy_fixed_consumer_startup_proposal_cannot_become_an_edit_recommendation():
    state = {
        "phase": "ANALYZED",
        "analysis_sha256": "a" * 64,
        "policy_producers": [
            {"workflow_id": "owned-workflow", "source": {"producer_file": "app.py"}}
        ],
        "instrumentation": {
            "proposals": [
                {"source_file": "app.py", "status": "PLAN_ONLY"},
                {"source_file": "startup.py", "status": "PLAN_ONLY"},
            ]
        },
    }
    report = summarize(state, checks={}, dependencies={})
    actions = report["startup"]["review_commands"]
    assert len(actions) == 1 and actions[0]["argv"][5] == "startup.py"
    assert report["startup"]["fixed_policy_consumers"][0]["source_file"] == "app.py"
