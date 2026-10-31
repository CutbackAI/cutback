"""Polling bounds and authority guards apply before credentials or remote writes."""

from types import SimpleNamespace

import pytest
from support_agent import fresh_application

from cutback.agent import analyze, load_session
from cutback.core.workspace import write_private
from cutback.workflow_operator import run_workflow


@pytest.mark.parametrize(
    "options",
    [
        {"max_wait_seconds": 0},
        {"max_wait_seconds": True},
        {"max_wait_seconds": 3601},
        {"poll_seconds": float("nan")},
        {"poll_seconds": float("inf")},
        {"poll_seconds": True},
        {"poll_seconds": 0},
        {"max_polls": 1001},
        {"max_polls": True},
        {"local_worker": "yes"},
    ],
)
def test_invalid_bounds_reject_before_source_or_collector(tmp_path, options):
    with pytest.raises(ValueError, match="WORKFLOW_OPERATOR_"):
        run_workflow(tmp_path, None, **options)


def prepared(tmp_path):
    fresh_application(tmp_path)
    analyze(tmp_path)
    state = load_session(tmp_path)
    state.update(
        endpoint="http://127.0.0.1",
        project_id="owned",
        phase="QUEUED",
        selected_workflow=state["profiles"][0]["binding"]["workflow_id"],
        search_mode="qualified_model_swap",
        plan={"id": "plan", "plan_hash": "a" * 64},
        run_id="run",
    )
    write_private(tmp_path / ".cutback/agent/session.json", state)
    return state


def test_pending_approval_and_terminal_states_never_call_collector(tmp_path):
    state = prepared(tmp_path)

    class Forbidden:
        endpoint = state["endpoint"]

        def __call__(self, *args):
            pytest.fail("NO_IO_FOR_PENDING_OR_TERMINAL")

    for phase, code in [
        ("PLAN_READY", "APPROVAL_REQUIRED"),
        ("COMPLETED", "STAGE_FINISHED"),
        ("STOPPED", "ACTION_REQUIRED"),
    ]:
        state["phase"] = phase
        write_private(tmp_path / ".cutback/agent/session.json", state)
        result = run_workflow(tmp_path, Forbidden(), local_worker=True)
        assert result["operator"]["code"] == code
        assert result["operator"]["local_worker_attempts"] == result["operator"]["polls"] == 0


def test_wait_bound_is_monotonic_and_progress_has_only_safe_fields(tmp_path, monkeypatch):
    from cutback import workflow_operator

    state = prepared(tmp_path)
    clock = {"value": 0.0}
    monkeypatch.setattr(
        workflow_operator,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock["value"],
            sleep=lambda duration: clock.update(value=clock["value"] + duration),
        ),
    )
    requests, progress = [], []

    def advance(root, control):
        control("GET", "/v1/projects/owned/experiments/run")
        return load_session(root)

    class Control:
        endpoint = state["endpoint"]

        def __call__(self, *args):
            requests.append(args)
            return {}

    monkeypatch.setattr("cutback.agent.advance", advance)
    result = run_workflow(
        tmp_path, Control(), max_wait_seconds=1, poll_seconds=0.5, emit=progress.append
    )
    assert result["operator"]["code"] == "LOCAL_WAIT_BOUND_REACHED"
    assert result["operator"]["polls"] == len(requests) == 2
    assert clock["value"] == 1
    assert all(
        set(row)
        == {
            "event",
            "polls",
            "local_worker_attempts",
            "active",
            "approval_required",
            "stage_finished",
        }
        for row in progress
    )
    assert all(type(value) in {str, bool, int} for row in progress for value in row.values())


@pytest.mark.parametrize(
    "change",
    [
        "id",
        "project_id",
        "endpoint",
        "analysis_sha256",
        "selected_workflow",
        "search_mode",
        "plan",
        "run_id",
    ],
)
@pytest.mark.parametrize("terminal", [False, True])
def test_saved_scope_cannot_switch_between_polls(tmp_path, monkeypatch, change, terminal):
    from cutback import workflow_operator

    state = prepared(tmp_path)
    calls = []

    def wait(_):
        state[change] = (
            {"id": "different", "plan_hash": "b" * 64} if change == "plan" else "different"
        )
        if terminal:
            state["phase"] = "COMPLETED"
        write_private(tmp_path / ".cutback/agent/session.json", state)

    monkeypatch.setattr(workflow_operator.time, "sleep", wait)
    monkeypatch.setattr("cutback.agent.advance", lambda root, control: calls.append(True) or state)
    with pytest.raises(ValueError, match="WORKFLOW_OPERATOR_.*CHANGED"):
        run_workflow(tmp_path, SimpleNamespace(endpoint=state["endpoint"]), poll_seconds=0.1)
    assert calls == [True]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/projects/owned/experiments",
        "/v1/projects/owned/experiments/plans/plan/approve",
        "/v1/projects/other/experiments/run",
    ],
)
def test_control_boundary_blocks_approval_queue_and_other_tenants(tmp_path, monkeypatch, path):
    state = prepared(tmp_path)

    class Forbidden:
        endpoint = state["endpoint"]

        def __call__(self, *args):
            pytest.fail("FORBIDDEN_WRITE_OR_FOREIGN_SCOPE")

    monkeypatch.setattr("cutback.agent.advance", lambda root, control: control("POST", path, {}))
    with pytest.raises(
        ValueError, match="WORKFLOW_OPERATOR_(CANNOT_APPROVE_OR_QUEUE|COLLECTOR_SCOPE_CHANGED)"
    ):
        run_workflow(tmp_path, Forbidden())


def test_source_mutation_stops_before_remote_poll_or_local_worker(tmp_path, monkeypatch):
    state = prepared(tmp_path)
    (tmp_path / "app.py").write_text("# changed source\n", encoding="utf-8")
    monkeypatch.setattr("cutback.agent.advance", lambda *a, **k: pytest.fail("NO_POLL"))
    monkeypatch.setattr(
        "cutback.workflow_operator.work_local", lambda *a, **k: pytest.fail("NO_WORK")
    )
    with pytest.raises(ValueError, match="SOURCE_CHANGED_REANALYZE"):
        run_workflow(tmp_path, SimpleNamespace(endpoint=state["endpoint"]), local_worker=True)


def test_collector_failure_propagates_once_without_local_execution(tmp_path, monkeypatch):
    state = prepared(tmp_path)
    calls = []

    class Uncertain:
        endpoint = state["endpoint"]

        def __call__(self, *args):
            calls.append(True)
            raise ValueError("COLLECTOR_RESPONSE_UNCERTAIN_INSPECT_SAVED_STATE")

    monkeypatch.setattr(
        "cutback.workflow_operator.work_local", lambda *a, **k: pytest.fail("NO_WORK")
    )
    with pytest.raises(ValueError, match="COLLECTOR_RESPONSE_UNCERTAIN"):
        run_workflow(tmp_path, Uncertain(), local_worker=True)
    assert calls == [True]


def test_core_cli_exposes_wait_and_local_worker_without_importing_runtime():
    from typer.testing import CliRunner

    from cutback.cli import app

    result = CliRunner().invoke(app, ["workflow", "run", "--help"])
    assert result.exit_code == 0
    import re

    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert all(
        option in visible for option in ("--local-worker", "--max-polls", "--max-wait-seconds")
    )


def test_source_preflight_cannot_consume_wait_then_start_remote_work(tmp_path, monkeypatch):
    from cutback import workflow_operator

    state = prepared(tmp_path)
    clock = {"value": 0.0}
    monkeypatch.setattr(
        workflow_operator, "time", SimpleNamespace(monotonic=lambda: clock["value"])
    )
    monkeypatch.setattr(workflow_operator, "preflight", lambda *a: clock.update(value=2.0))
    monkeypatch.setattr("cutback.agent.advance", lambda *a, **k: pytest.fail("NO_LATE_POLL"))
    result = run_workflow(tmp_path, SimpleNamespace(endpoint=state["endpoint"]), max_wait_seconds=1)
    assert result["operator"]["code"] == "LOCAL_WAIT_BOUND_REACHED"
    assert result["operator"]["polls"] == result["operator"]["local_worker_attempts"] == 0


@pytest.mark.parametrize("failure", ["construction", "query"])
def test_local_database_outage_suppresses_driver_details_and_disposes_owned_engine(
    tmp_path,
    monkeypatch,
    failure,
):
    import os

    from sqlalchemy.exc import OperationalError

    from cutback.workflow_operator import work_local

    state = prepared(tmp_path)
    disposed = []
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    monkeypatch.setattr(
        "cutback.local_stack.configured",
        lambda root: (
            root,
            {
                "endpoint": state["endpoint"],
                "allow_payloads": True,
            },
        ),
    )
    monkeypatch.setattr("cutback.local_stack.database_url", lambda *a: "synthetic-owned-db")

    def unavailable(*args, **kwargs):
        raise OperationalError(
            "synthetic-private-driver-detail", {}, RuntimeError("private-driver-message")
        )

    monkeypatch.setattr(
        "cutback.server.db.database",
        unavailable
        if failure == "construction"
        else lambda *a: (SimpleNamespace(dispose=lambda: disposed.append(True)), unavailable),
    )
    with pytest.raises(ValueError) as error:
        work_local(tmp_path)
    assert str(error.value) == "LOCAL_OPERATOR_DATABASE_UNAVAILABLE_INSPECT_OWNED_STACK"
    assert disposed == ([] if failure == "construction" else [True])
    assert os.environ["CUTBACK_ALLOW_PAYLOADS"] == "false"
