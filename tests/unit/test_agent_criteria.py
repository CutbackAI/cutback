"""Recovery and local dispatch preserve the same approved operation."""

import base64
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from cutback.agent import next_action
from cutback.cli import app


@pytest.mark.parametrize(
    "phase,tool",
    [
        ("PREPARING_CRITERIA", "criteria-prepare"),
        ("APPROVING_CRITERIA", "continue"),
        ("QUEUEING_CRITERIA", "continue"),
        ("CRITERIA_QUEUED", "continue"),
        ("CRITERIA_RUNNING", "continue"),
        ("CRITERIA_REVIEW_REQUIRED", "criteria-read"),
        ("REVIEWING_CRITERIA", "criteria-review"),
        ("PREPARING_REVIEWED_VALIDATION", "criteria-review"),
        ("PREPARING_CONFIRMATION", "confirm"),
    ],
)
def test_recovery_actions_reuse_intent_without_automatic_billable_retry(phase, tool):
    action = next_action({"phase": phase, "search_mode": "reviewed_criteria"})
    assert action["tool"] == "cutback workflow " + tool
    assert action.get("automatic_billable_retry", False) is False


def test_local_criteria_worker_dispatches_only_saved_run_and_preserves_operator_environment(
    tmp_path, monkeypatch
):
    import os

    import cutback.agent
    import cutback.local_stack
    import cutback.server.criteria_worker
    import cutback.server.db

    run_id = uuid4()
    state = {
        "phase": "CRITERIA_QUEUED",
        "endpoint": "http://127.0.0.1:8080",
        "criteria": {"generation": {"generation_run_id": str(run_id)}},
    }
    monkeypatch.setattr(cutback.agent, "load_session", lambda root: state)
    config = {
        "endpoint": state["endpoint"],
        "allow_payloads": True,
        "payload_key": base64.b64encode(b"x" * 32).decode(),
    }
    monkeypatch.setattr(cutback.local_stack, "configured", lambda root: (tmp_path, config))
    monkeypatch.setattr(cutback.local_stack, "database_url", lambda *args: "owned-test-only")
    disposed = []

    class Engine:
        def dispose(self):
            disposed.append(True)

    sessions = object()
    monkeypatch.setattr(cutback.server.db, "database", lambda url: (Engine(), sessions))
    calls = []

    def run(actual_sessions, store, *, execution_enabled, only_run_id):
        assert actual_sessions is sessions and execution_enabled is True
        assert only_run_id == run_id and os.environ["CUTBACK_ALLOW_PAYLOADS"] == "true"
        calls.append(only_run_id)
        return {"status": "COMPLETED"}

    monkeypatch.setattr(cutback.server.criteria_worker, "run_criteria_one", run)
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "owned-original-value")
    result = CliRunner().invoke(app, ["workflow", "work", "--target", str(tmp_path)])
    assert result.exit_code == 0 and calls == [run_id] and disposed == [True]
    assert os.environ["CUTBACK_ALLOW_PAYLOADS"] == "owned-original-value"
    state["phase"] = "CRITERIA_PLAN_READY"
    denied = CliRunner().invoke(app, ["workflow", "work", "--target", str(tmp_path)])
    assert denied.exit_code != 0 and calls == [run_id]


def test_private_criteria_read_requires_explicit_output(monkeypatch):
    import cutback.agent_control

    def forbidden_connection(*args, **kwargs):
        raise AssertionError("PRIVATE_READ_MUST_REQUIRE_OUTPUT_BEFORE_CONNECTING")

    monkeypatch.setattr(cutback.agent_control, "connect", forbidden_connection)
    result = CliRunner().invoke(app, ["workflow", "criteria-read", "--purpose-reference", "owned"])
    assert result.exit_code == 2


def test_private_review_output_is_atomic_and_refuses_redirected_parent(tmp_path):
    import json
    import os
    import stat

    from cutback.core.workspace import write_private_output

    output = tmp_path / "private/checks.json"
    write_private_output(output, {"owned_private_check": "synthetic only"})
    assert json.loads(output.read_text(encoding="utf-8"))["owned_private_check"] == "synthetic only"
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "redirect"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable; regular private output verified")
    with pytest.raises(ValueError, match="UNSAFE_PRIVATE_OUTPUT_DIRECTORY"):
        write_private_output(link / "checks.json", {"private": "must not escape"})
    assert not list(outside.iterdir())
