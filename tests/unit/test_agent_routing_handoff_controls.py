"""Actual local handoff controls; canonical server authority is simulated only in this scope."""

from uuid import uuid4

import pytest
from test_routing_source_changes import application, proposal

import cutback.agent_routing_handoff as handoff
from cutback.application_checks import git, snapshot
from cutback.core.workspace import read_private, write_private
from cutback.routing_changes import prepare_patch, review_change


def setup_journey(tmp_path, monkeypatch):
    root = application(tmp_path)
    (root / "check_application.py").write_text(
        "import json,os\nfrom pathlib import Path\n"
        "def main():\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n"
        "    assert json.loads(Path('routing.json').read_text())['provider'] == 'router'\n"
        "    assert os.environ.get('CUTBACK_FIXTURE_FAIL') != '1'\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    git(root, "add", "check_application.py")
    git(
        root,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent application consumer check",
    )
    frozen = proposal(root)
    program = {"program_id": str(uuid4()), "program_hash": "c" * 64}
    revoked, fetched = [False], []

    def current_proposal(actual, control, *, purpose):
        fetched.append(purpose)
        if revoked[0]:
            raise ValueError("OWNED_CURRENT_RIGHTS_REVOKED")
        return prepare_patch(actual, frozen)

    def refreshed_review(
        actual, control, *, purpose, contract, receipt, proposed_root, coverage, branch
    ):
        artifact = current_proposal(actual, control, purpose=purpose)
        return review_change(
            artifact, contract, receipt, actual, proposed_root, coverage, branch=branch
        )

    monkeypatch.setattr("cutback.agent_routing_program.current", lambda *args: program)
    monkeypatch.setattr("cutback.agent_routing_program.proposal", current_proposal)
    monkeypatch.setattr("cutback.agent_routing_program.review", refreshed_review)
    monkeypatch.setenv("OPENAI_API_KEY", "owned-ambient-never-in-checks")
    return root, tmp_path / "private-review", program, revoked, fetched


@pytest.fixture
def journey(tmp_path, monkeypatch):
    return setup_journey(tmp_path, monkeypatch)


def invoke(journey, **changes):
    root, destination, *_ = journey
    changes.setdefault("branch", "cutback/test-routing-handoff")
    return handoff.finish(
        root, object(), destination=destination, purpose="owned-local-handoff", **changes
    )


def test_discovery_preview_then_actual_checks_review_and_resume_without_reexecution(
    journey, monkeypatch
):
    root, destination, *_ = journey
    original = snapshot(root)
    preview = invoke(journey)
    assert preview["status"] == "CHECKS_READY" and preview["application_checks_executed"] is False
    assert preview["owner_consumer_coverage_required"] is True and len(preview["command_keys"]) == 1
    coverage = {"ticket-priority": preview["command_keys"]}
    result = invoke(journey, coverage=coverage, execute_local=True)
    assert result["status"] == "REVIEWED" and result["both_application_trees_checked"] is True
    assert result["local_review_ref"] == "refs/heads/cutback/test-routing-handoff"
    assert result["byte_exact_rollback_checked"] is True and snapshot(root) == original
    assert read_private(destination / "checks.json")["all_passed"] is True

    def no_second_execution(*args, **kwargs):
        raise AssertionError("completed checks must be reused")

    monkeypatch.setattr(handoff, "run_checks", no_second_execution)
    assert invoke(journey, execute_local=True)["status"] == "REVIEWED"
    assert invoke(journey)["application_checks_executed"] is True
    (destination / "review.json").unlink()
    with pytest.raises(ValueError, match="REVIEW_RECEIPT_MISSING"):
        invoke(journey)
    assert invoke(journey, execute_local=True)["status"] == "REVIEWED"
    (destination / "checks.json").unlink()
    with pytest.raises(ValueError, match="CHECK_REPLY_UNCERTAIN_NO_AUTOMATIC_REPLAY"):
        invoke(journey)
    assert snapshot(root) == original


def test_uncertain_dispatch_without_receipt_stops_instead_of_replaying(journey, monkeypatch):
    root, _, program, *_ = journey
    preview = invoke(journey)
    coverage = {"ticket-priority": preview["command_keys"]}
    invoke(journey, coverage=coverage)
    journal = root / ".cutback/agent" / ("routing-handoff-" + program["program_id"] + ".json")
    state = read_private(journal)
    state["phase"] = "CHECKS_DISPATCHED"
    write_private(journal, state)
    calls = []
    monkeypatch.setattr(handoff, "run_checks", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ValueError, match="CHECK_REPLY_UNCERTAIN_NO_AUTOMATIC_REPLAY"):
        invoke(journey, execute_local=True)
    assert not calls


def test_lost_receipt_write_ack_reconciles_completed_checks_without_reexecution(
    journey, monkeypatch
):
    preview = invoke(journey)
    coverage = {"ticket-priority": preview["command_keys"]}
    write = handoff.write_private
    lost = []

    def lose_reply(path, value, **kwargs):
        result = write(path, value, **kwargs)
        if path.name == "checks.json" and not lost:
            lost.append(True)
            raise ConnectionError("owned lost local receipt-write acknowledgement")
        return result

    monkeypatch.setattr(handoff, "write_private", lose_reply)
    with pytest.raises(ConnectionError):
        invoke(journey, coverage=coverage, execute_local=True)

    def no_second_execution(*args, **kwargs):
        raise AssertionError("known receipt must reconcile the same dispatch")

    monkeypatch.setattr(handoff, "run_checks", no_second_execution)
    assert invoke(journey, execute_local=True)["status"] == "REVIEWED"


def test_revoked_confirmation_after_checks_prevents_review_and_preserves_known_receipt(
    journey, monkeypatch
):
    _, destination, _, revoked, _ = journey
    preview = invoke(journey)
    coverage = {"ticket-priority": preview["command_keys"]}
    run = handoff.run_checks

    def revoke_after_checks(*args, **kwargs):
        result = run(*args, **kwargs)
        revoked[0] = True
        return result

    monkeypatch.setattr(handoff, "run_checks", revoke_after_checks)
    with pytest.raises(ValueError, match="OWNED_CURRENT_RIGHTS_REVOKED"):
        invoke(journey, coverage=coverage, execute_local=True)
    assert read_private(destination / "checks.json")["all_passed"] is True
    assert not (destination / "review.json").exists()


def test_failed_application_check_never_becomes_a_local_review(journey, monkeypatch):
    commands = [
        {
            "key": "consumer",
            "arguments": ["check_application.py"],
            "environment": {"CUTBACK_FIXTURE_FAIL": "1"},
        }
    ]
    result = invoke(
        journey, commands=commands, coverage={"ticket-priority": ["consumer"]}, execute_local=True
    )
    assert result["status"] == "CHECKS_FAILED"
    assert not (journey[1] / "review.json").exists()
    with pytest.raises(ValueError, match="FAILED_CHECKS_REQUIRE_EXPLICIT_NEW_SOURCE_REVIEW"):
        invoke(journey, commands=commands, execute_local=True)


@pytest.mark.parametrize("problem", ["inside", "existing"])
def test_output_boundary_precedes_content_read_and_existing_files_survive(journey, problem):
    root, destination, _, _, fetched = journey
    if problem == "inside":
        destination = root / "private-review"
    else:
        destination.mkdir()
        (destination / "preserve.txt").write_text("preserve", encoding="utf-8")
    with pytest.raises(ValueError):
        handoff.finish(root, object(), destination=destination, purpose="owned-output-boundary")
    assert not fetched
    if problem == "existing":
        assert (destination / "preserve.txt").read_text() == "preserve"


def test_interrupted_private_rollback_preserves_copy_and_resumes_nonbillable_preparation(
    journey, monkeypatch
):
    root, destination, program, *_ = journey
    original = snapshot(root)
    real = handoff.rollback_check

    def interrupted(artifact, actual, proposed, output, frozen):
        patch = output / "interrupted-rollback.patch"
        patch.write_text(artifact["rollback_diff"], encoding="utf-8", newline="")
        git(proposed, "apply", str(patch))
        raise ConnectionError("owned interruption after private inverse application")

    monkeypatch.setattr(handoff, "rollback_check", interrupted)
    with pytest.raises(ConnectionError):
        invoke(journey)
    assert (destination / "attempt-1/workspaces.json").exists()
    monkeypatch.setattr(handoff, "rollback_check", real)
    preview = invoke(journey)
    assert preview["status"] == "CHECKS_READY" and not preview["application_checks_executed"]
    journal = root / ".cutback/agent" / ("routing-handoff-" + program["program_id"] + ".json")
    assert read_private(journal)["preparations"] == 2
    assert (destination / "attempt-1/workspaces.json").exists()
    assert (destination / "attempt-2/workspaces.json").exists()
    assert snapshot(root) == original
