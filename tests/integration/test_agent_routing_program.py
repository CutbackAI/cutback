"""Lost replies retain the same program; source checks and rollback stay exact."""

import shutil
from uuid import UUID

import pytest
from integration.test_routing_program_api import prepared_program
from integration.test_routing_program_workflow import native
from sqlalchemy import func, select

from cutback.agent import advance as advance_stage
from cutback.agent import load_session
from cutback.agent import proposal as workflow_proposal
from cutback.agent_control import Control
from cutback.agent_routing_program import advance, current, prepare, review, run, status
from cutback.application_checks import (
    ApplicationCommand,
    git,
    run_checks,
)
from cutback.application_checks import (
    prepare as checks_contract,
)
from cutback.core.workspace import read_private
from cutback.routing_program_operator import RoutingProgramOperator
from cutback.server.routing_program_db import RoutingProgramRecord, RoutingProgramRun

pytestmark = pytest.mark.postgres


def test_lost_program_and_approval_reply_reconcile_without_second_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    _, root, _, _, request = prepared_program(api, tenant, tmp_path, monkeypatch)
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])

    class Lost:
        endpoint = control.endpoint

        def __init__(self):
            self.lost, self.approvals = set(), 0

        def __call__(self, method, path, body=None):
            value = control(method, path, body)
            if method == "POST" and path.endswith("/approve"):
                self.approvals += 1
            kind = (
                "prepare"
                if method == "POST" and path.endswith("/routing/programs")
                else ("approve" if method == "POST" and path.endswith("/approve") else None)
            )
            if kind and kind not in self.lost:
                self.lost.add(kind)
                raise ConnectionError("owned lost commit acknowledgement")
            return value

    lost = Lost()
    args = dict(
        baseline_snapshot_id=request["baseline_snapshot_id"],
        max_usd="100",
        max_requests=2000,
        max_wall_seconds=300,
        savings_per_success="0.01",
        forecast_successful_tasks=100000,
    )
    with pytest.raises(ConnectionError):
        prepare(root, lost, **args)
    row = prepare(root, lost, **args)
    assert prepare(root, lost, **args)["id"] == row["id"]
    with pytest.raises(ValueError, match="FROZEN_INTENT_CHANGED"):
        prepare(root, lost, **{**args, "max_requests": 1999})
    with pytest.raises(ValueError, match="ROUTING_PROGRAM_OWNS_TEMPLATE"):
        advance_stage(root, lost)
    with pytest.raises(ConnectionError):
        advance(root, lost, program_hash=row["program_hash"], approval_ref="owned-one-approval")
    assert advance(root, lost)["status"] == "QUEUED"
    assert advance(root, lost)["id"] == row["id"] and lost.approvals == 1
    with pytest.raises(ValueError, match="POLL_AND_WAIT_BOUNDS"):
        run(root, lost, max_polls=0)
    assert run(root, lost, max_polls=1, poll_seconds=0.1)["operator_code"].startswith("POLL_BOUND")
    assert load_session(root)["routing_program"]["program_id"] == row["id"]
    saved = read_private(root / ".cutback/agent/routing-program.json")
    assert "token" not in saved
    with api[2].state.sessions() as session:
        parent = session.get(RoutingProgramRecord, UUID(row["id"]))
        assert parent.requests_used == 0
        assert not session.scalar(
            select(RoutingProgramRun.id).where(RoutingProgramRun.program_id == parent.id)
        )


def test_program_operator_to_actual_both_tree_checks_and_byte_exact_rollback(
    api, tenant, tmp_path, monkeypatch
):
    _, root, store, _, request = prepared_program(api, tenant, tmp_path, monkeypatch)
    control = Control("http://127.0.0.1", api[1]["Authorization"][7:], client=api[0])
    row = prepare(
        root,
        control,
        baseline_snapshot_id=request["baseline_snapshot_id"],
        max_usd="100",
        max_requests=2000,
        max_wall_seconds=300,
        savings_per_success="0.01",
        forecast_successful_tasks=100000,
    )
    advance(root, control, program_hash=row["program_hash"], approval_ref="owned-program-only")
    executor, calls = native(monkeypatch, simple_passes=True)
    try:
        operator = RoutingProgramOperator(
            api[2].state.sessions,
            store,
            project_id=tenant[0],
            program_id=row["id"],
            program_hash=row["program_hash"],
            executor=executor,
            preflight=lambda: current(root, control),
        )
        result = operator.run(max_operations=4, max_wait_seconds=120, poll_seconds=0.1)
        assert result["status"] == "COMPLETED", status(root, control).get("summary")
        assert result["local_child_attempts"] == 2
        count = len(calls)
        assert operator.step()["operator_code"] == "TERMINAL" and len(calls) == count
        artifact = workflow_proposal(root, control, purpose="owned-final-source-diff")
        proposed = tmp_path / "proposed"
        shutil.copytree(root, proposed, ignore=shutil.ignore_patterns(".cutback", "__pycache__"))
        patch = tmp_path / "program.patch"
        patch.write_text(artifact["diff"], encoding="utf-8", newline="")
        git(proposed, "apply", "--check", str(patch))
        git(proposed, "apply", str(patch))
        git(proposed, "add", "routing.json")
        git(proposed, "commit", "-m", "Review independent static routing program")
        contract = checks_contract(
            root,
            proposed,
            [ApplicationCommand(key="native-application", arguments=["check_application.py"])],
        )
        receipt = run_checks(contract, root, proposed, execute_local=True)
        result = review(
            root,
            control,
            purpose="owned-final-source-review",
            contract=contract,
            receipt=receipt,
            proposed_root=proposed,
            coverage={"ticket-priority": ["native-application"]},
            branch="cutback/owned-program-review",
        )
        assert result["local_review_ref"] == "refs/heads/cutback/owned-program-review"
        rollback = tmp_path / "rollback.patch"
        rollback.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
        git(proposed, "apply", "--check", str(rollback))
        git(proposed, "apply", str(rollback))
        assert (proposed / "routing.json").read_bytes() == (root / "routing.json").read_bytes()
        assert len(calls) == count
        with api[2].state.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(RoutingProgramRun)
                    .where(RoutingProgramRun.program_id == UUID(row["id"]))
                )
                == 2
            )
    finally:
        executor.close()
