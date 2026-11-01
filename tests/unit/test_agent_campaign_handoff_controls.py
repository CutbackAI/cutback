"""Actual private source/check execution; collector authority simulated in this scope."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from test_application_patch import patch

import cutback.agent_campaign_handoff as handoff
from cutback.application_checks import git, snapshot
from cutback.campaign_review import review_change
from cutback.core.workspace import read_private


@pytest.fixture
def journey(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    before, after = b'MODEL = "baseline"\n', b'MODEL = "cheap"\n'
    (root / "sample.py").write_bytes(before)
    (root / ".gitignore").write_text(".cutback/\n__pycache__/\n", encoding="utf-8")
    (root / "check_application.py").write_text(
        "import os\nfrom sample import MODEL\n"
        "def main():\n"
        "    assert MODEL in ('baseline','cheap')\n"
        "    assert 'OPENAI_API_KEY' not in os.environ\n"
        "    assert os.environ.get('CUTBACK_FIXTURE_FAIL') != '1'\n"
        "if __name__ == '__main__':\n    main()\n",
        encoding="utf-8",
    )
    git(root, "init", "--template=")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent synthetic source",
    )
    campaign = {
        "campaign_id": str(uuid4()),
        "project_id": str(uuid4()),
        "endpoint": "http://127.0.0.1",
    }
    diff = patch(before, after, "sample.py")
    artifact = {
        **campaign,
        "campaign_hash": "c" * 64,
        "status": "DRY_RUN_ONLY",
        "production_deployed": False,
        "run_id": str(uuid4()),
        "plan_hash": "b" * 64,
        "confirmation_gate": {"passed": True, "scope": "sample_only", "plan_hash": "b" * 64},
        "diff": diff,
        "rollback_diff": patch(after, before, "sample.py"),
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "source_file": "sample.py",
        "source_sha256": hashlib.sha256(before).hexdigest(),
        "source_commit": snapshot(root).commit,
        "verification": {"after_sha256": hashlib.sha256(after).hexdigest()},
        "affected_workflows": ["classify", "route"],
        "unresolved_consumer_workflows": [],
        "scanner_warning_codes": [],
    }
    revoked, fetched = [False], []

    def proposal(root, control, *, purpose, sample_only):
        fetched.append(purpose)
        if revoked[0]:
            raise ValueError("OWNED_CURRENT_RIGHTS_REVOKED")
        if not sample_only:
            raise ValueError("CAMPAIGN_INDEPENDENT_CONFIRMATION_GATE_REQUIRED")
        return artifact

    def review(
        root, control, *, purpose, contract, receipt, base, proposed, coverage, branch, sample_only
    ):
        current = proposal(root, control, purpose=purpose, sample_only=sample_only)
        return review_change(current, contract, receipt, base, proposed, coverage, branch=branch)

    monkeypatch.setattr("cutback.agent_campaign.current", lambda *args: campaign)
    monkeypatch.setattr("cutback.agent_campaign.proposal", proposal)
    monkeypatch.setattr("cutback.agent_campaign.review", review)
    monkeypatch.setenv("OPENAI_API_KEY", "owned-ambient-must-not-enter-check")
    return root, tmp_path / "private-review", campaign, revoked, fetched


def invoke(journey, **changes):
    changes.setdefault("sample_only", True)
    return handoff.finish(
        journey[0],
        object(),
        destination=journey[1],
        purpose="owned-local-check",
        branch="cutback/native-review",
        **changes,
    )


def coverage(preview):
    return {workflow: preview["command_keys"] for workflow in preview["affected_workflows"]}


def test_native_preview_exact_consumers_checks_review_and_no_replay(journey, monkeypatch):
    root, destination, *_ = journey
    original = snapshot(root)
    preview = invoke(journey)
    assert preview["affected_workflows"] == ["classify", "route"]
    assert preview["application_checks_executed"] is False
    result = invoke(journey, coverage=coverage(preview), execute_local=True)
    assert result["status"] == "REVIEWED" and result["both_application_trees_checked"]
    assert snapshot(root) == original
    assert read_private(destination / "review.json")["evidence_scope"] == "sample_only"

    def no_second_execution(*args, **kwargs):
        raise AssertionError("completed check dispatch must not repeat")

    monkeypatch.setattr(handoff, "run_checks", no_second_execution)
    assert invoke(journey)["application_checks_executed"] is True
    assert invoke(journey, execute_local=True)["status"] == "REVIEWED"
    proposed = Path(result["proposed_root"])
    git(proposed, "update-ref", "-d", "refs/heads/cutback/native-review")
    with pytest.raises(ValueError, match="REVIEW_REF_CHANGED"):
        invoke(journey)


def test_incomplete_or_unrelated_consumer_coverage_stops_before_checks(journey, monkeypatch):
    preview = invoke(journey)
    calls = []
    monkeypatch.setattr(handoff, "run_checks", lambda *args, **kwargs: calls.append(True))
    for declared in ({"classify": preview["command_keys"]}, {"unrelated": preview["command_keys"]}):
        with pytest.raises(ValueError, match="ALL_AFFECTED_CONSUMERS_REQUIRE_COVERAGE"):
            invoke(journey, coverage=declared, execute_local=True)
    assert not calls


def test_native_lost_receipt_acknowledgement_reconciles_without_second_check(journey, monkeypatch):
    preview, write, lost = invoke(journey), handoff.write_private, []

    def lose(path, value, **options):
        result = write(path, value, **options)
        if path.name == "checks.json" and not lost:
            lost.append(True)
            raise ConnectionError("owned receipt acknowledgement loss")
        return result

    monkeypatch.setattr(handoff, "write_private", lose)
    with pytest.raises(ConnectionError):
        invoke(journey, coverage=coverage(preview), execute_local=True)

    def no_second_execution(*args, **kwargs):
        raise AssertionError("known receipt must reconcile the same check dispatch")

    monkeypatch.setattr(handoff, "run_checks", no_second_execution)
    assert invoke(journey, execute_local=True)["status"] == "REVIEWED"


def test_native_current_rights_after_checks_and_sample_scope_cannot_upgrade(journey, monkeypatch):
    preview, run = invoke(journey), handoff.run_checks

    def revoked_after(*args, **kwargs):
        result = run(*args, **kwargs)
        journey[3][0] = True
        return result

    monkeypatch.setattr(handoff, "run_checks", revoked_after)
    with pytest.raises(ValueError, match="OWNED_CURRENT_RIGHTS_REVOKED"):
        invoke(journey, coverage=coverage(preview), execute_local=True)
    assert read_private(journey[1] / "checks.json")["all_passed"]
    assert not (journey[1] / "review.json").exists()
    journey[3][0] = False
    with pytest.raises(ValueError, match="INDEPENDENT_CONFIRMATION_GATE_REQUIRED"):
        invoke(journey, execute_local=True, sample_only=False)


@pytest.mark.parametrize("field", ["project_id", "endpoint"])
def test_saved_project_or_collector_change_cannot_reuse_a_prepared_handoff(
    journey, monkeypatch, field
):
    preview = invoke(journey)
    journey[2][field] = str(uuid4()) if field == "project_id" else "http://127.0.0.1:54321"
    calls = []
    monkeypatch.setattr(handoff, "run_checks", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(ValueError, match="FROZEN_INTENT_CHANGED"):
        invoke(journey, coverage=coverage(preview), execute_local=True)
    assert not calls and not (journey[1] / "checks.json").exists()
