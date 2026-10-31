"""Real parse-only discovery explains blockers without executing or disclosing source."""

import json
import socket

import pytest
from support_agent import fresh_application
from typer.testing import CliRunner

from cutback.agent import analyze
from cutback.cli import app
from cutback.core.source_guidance import explain_source, render_source_guide, request_guidance


@pytest.mark.parametrize(
    "call,expected",
    [
        ('model="gpt-4.1", input=text', "REQUEST_PAYLOAD_AND_EXPLICIT_OUTPUT_BOUND_REQUIRED"),
        (
            'model="gpt-4.1", input=text, max_output_tokens=64, **hidden_options',
            "EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW",
        ),
        (
            'model="gpt-4.1", input=text, max_output_tokens=64, instructions=hidden(text)',
            "DYNAMIC_REQUEST_VALUE_REQUIRES_REVIEW",
        ),
        (
            'model="gpt-4.1", input=text, max_output_tokens=64, '
            'secret_custom_option="untrusted-sentinel"',
            "UNQUALIFIED_REQUEST_OPTIONS",
        ),
    ],
)
def test_offline_blocker_guides_are_actionable_and_do_not_leak_arguments(
    tmp_path, monkeypatch, call, expected
):
    def forbid(*args, **kwargs):
        raise AssertionError("NETWORK_OR_APPLICATION_EXECUTION_FORBIDDEN")

    monkeypatch.setattr(socket, "create_connection", forbid)
    source = (
        "from openai import OpenAI\nclient=OpenAI()\ndef classify(text):\n"
        f"    return client.responses.create({call})\n"
        'raise RuntimeError("untrusted-sentinel-must-not-execute")\n'
    )
    (tmp_path / "app.py").write_text(source)
    state = analyze(tmp_path)
    assert not state["profiles"] and len(state["unresolved"]) == 1
    issue = state["unresolved"][0]
    assert issue["diagnostic_code"] == expected
    assert issue["summary"] and issue["next_action"] and issue["replay_qualified"] is False
    assert state["next_action"]["code"] == "INSPECT_UNQUALIFIED_SOURCE_CONTRACTS"
    result = CliRunner().invoke(app, ["workflow", "explain", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert "Qualified request profiles: 0" in result.stdout
    assert issue["summary"] in result.stdout and "cutback workflow explain" in result.stdout
    structured = CliRunner().invoke(
        app, ["workflow", "explain", "--target", str(tmp_path), "--json"]
    )
    assert structured.exit_code == 0
    report = json.loads(structured.stdout)
    assert report["provider_calls"] == 0 and report["source_changed"] is None
    assert report["source_edit_performed"] is False
    assert report["source_freshness"] == "not_rechecked_saved_snapshot_only"
    assert "untrusted-sentinel" not in result.stdout + structured.stdout + json.dumps(state)
    assert (tmp_path / "app.py").read_text() == source


def test_known_option_prefix_never_echoes_unknown_keys_or_arbitrary_exception_text():
    for error in (
        ValueError("UNQUALIFIED_REQUEST_OPTIONS:private-option,token-value"),
        ValueError("arbitrary-source-and-credential"),
        OSError("private-file-path"),
    ):
        guide = request_guidance(error)
        output = json.dumps(guide)
        assert all(
            word not in output
            for word in ("private-option", "token-value", "arbitrary-source", "private-file")
        )
        assert guide["replay_qualified"] is False


def test_supported_source_keeps_preparation_path_and_explain_reads_saved_snapshot(tmp_path):
    fresh_application(tmp_path)
    state = analyze(tmp_path)
    assert state["next_action"]["code"] == "REVIEW_DATA_RIGHTS_AND_PREPARE"
    original = (tmp_path / "app.py").read_text()
    (tmp_path / "app.py").write_text("raise RuntimeError('do not execute or reanalyze')")
    result = CliRunner().invoke(app, ["workflow", "explain", "--target", str(tmp_path), "--json"])
    assert result.exit_code == 0
    report = json.loads(result.stdout)
    assert report["qualified_request_profiles"] == 1 and report["issues"] == []
    assert report["source_freshness"] == "not_rechecked_saved_snapshot_only"
    (tmp_path / "app.py").write_text(original)


def test_no_detected_source_does_not_recommend_preparing_a_nonexistent_profile(tmp_path):
    state = analyze(tmp_path)
    assert not state["profiles"] and not state["unresolved"]
    assert state["next_action"]["tool"] == "cutback workflow explain"


def test_explanation_stays_bounded_and_does_not_echo_raw_stored_issue_details():
    report = explain_source(
        {
            "phase": "ANALYZED",
            "unresolved": [
                {
                    "source_file": "owned\nfilename.py",
                    "diagnostic_code": "unknown-private-text",
                    "next_action": "private-stored-detail",
                    "summary": "private-stored-detail",
                }
                for _ in range(22)
            ],
            "next_action": {
                "code": "INSPECT_UNQUALIFIED_SOURCE_CONTRACTS",
                "plan": "private-plan-body",
            },
        }
    )
    assert len(report["issues"]) == 20 and report["omitted_issues"] == 2
    text = render_source_guide(report)
    assert "owned\\nfilename.py" in text and "owned\nfilename.py" not in text
    assert "private-stored-detail" not in text and "private-plan-body" not in json.dumps(report)
