"""Native SDK/real PG confirmation to automated private workspaces/checks/local review."""

import pytest
from integration.test_routing_program_api import prepared_program
from integration.test_routing_program_workflow import native

import cutback.agent_routing_handoff as handoff
from cutback.agent import load_session
from cutback.agent_control import Control
from cutback.agent_routing_program import advance, current, prepare, status
from cutback.application_checks import snapshot
from cutback.core.workspace import read_private
from cutback.routing_program_operator import RoutingProgramOperator

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revoke_after_checks", [False, True])
def test_confirmed_program_finish_checks_and_current_authority_without_extra_native_spend(
    api, tenant, tmp_path, monkeypatch, revoke_after_checks
):
    base, root, store, _, request = prepared_program(api, tenant, tmp_path, monkeypatch)
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
    advance(root, control, program_hash=row["program_hash"], approval_ref="owned-one-program")
    executor, calls = native(monkeypatch, simple_passes=True)
    try:
        result = RoutingProgramOperator(
            api[2].state.sessions,
            store,
            project_id=tenant[0],
            program_id=row["id"],
            program_hash=row["program_hash"],
            executor=executor,
            preflight=lambda: current(root, control),
        ).run(max_operations=4, max_wait_seconds=120, poll_seconds=0.1)
        assert result["status"] == "COMPLETED", status(root, control).get("summary")
        original, before_calls = snapshot(root), len(calls)
        destination = tmp_path / "private-handoff"
        preview = handoff.finish(
            root,
            control,
            destination=destination,
            purpose="owned-preview",
            branch="cutback/review-automated-routing",
        )
        assert preview["status"] == "CHECKS_READY"
        assert preview["owner_consumer_coverage_required"] is True
        assert len(calls) == before_calls
        run = handoff.run_checks
        physical_checks = []

        def observed(*args, **kwargs):
            physical_checks.append(True)
            checked = run(*args, **kwargs)
            if revoke_after_checks:
                response = api[0].delete(
                    base + "/capture-policies/" + load_session(root)["policy_id"], headers=api[1]
                )
                assert response.status_code == 200
            return checked

        monkeypatch.setattr(handoff, "run_checks", observed)
        arguments = {
            "destination": destination,
            "purpose": "owned-final-handoff",
            "coverage": {"ticket-priority": preview["command_keys"]},
            "execute_local": True,
            "branch": "cutback/review-automated-routing",
        }
        if revoke_after_checks:
            with pytest.raises(
                ValueError, match="ROUTING_PROGRAM_CURRENT_CONFIRMED_SOURCE_REQUIRED"
            ):
                handoff.finish(root, control, **arguments)
            assert not (destination / "review.json").exists()
        else:
            reviewed = handoff.finish(root, control, **arguments)
            assert reviewed["status"] == "REVIEWED"
            assert reviewed["local_review_ref"] == "refs/heads/cutback/review-automated-routing"
            assert reviewed["both_application_trees_checked"] is True
            assert reviewed["byte_exact_rollback_checked"] is True
            assert handoff.finish(root, control, **arguments)["status"] == "REVIEWED"
        assert physical_checks == [True]
        assert read_private(destination / "checks.json")["all_passed"] is True
        assert snapshot(root) == original and len(calls) == before_calls
    finally:
        executor.close()
