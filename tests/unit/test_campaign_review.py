import hashlib

import pytest
from test_application_checks import repository

from cutback.application_checks import ApplicationCommand, git, prepare, run_checks
from cutback.campaign_review import review_change


def test_campaign_review_binds_exact_change_all_consumers_and_non_overwriting_local_ref(tmp_path):
    base = repository(tmp_path / "base", "print('A')\n")
    proposed = repository(tmp_path / "proposed", "print('B')\n")
    hook = proposed / ".git/hooks/reference-transaction"
    hook.write_text("#!/bin/sh\nprintf executed > OWNED_HOOK_MUST_NOT_RUN\n", encoding="utf-8")
    hook.chmod(0o755)
    contract = prepare(
        base, proposed, [ApplicationCommand(key="application-smoke", arguments=["sample.py"])]
    )
    receipt = run_checks(contract, base, proposed, execute_local=True)
    diff = "owned synthetic diff"
    artifact = {
        "status": "DRY_RUN_ONLY",
        "production_deployed": False,
        "campaign_id": "synthetic-campaign",
        "campaign_hash": "a" * 64,
        "run_id": "synthetic-run",
        "plan_hash": "b" * 64,
        "confirmation_gate": {"passed": True, "scope": "sample_only", "plan_hash": "b" * 64},
        "diff": diff,
        "rollback_diff": "owned synthetic rollback",
        "diff_sha256": hashlib.sha256(diff.encode()).hexdigest(),
        "source_file": "sample.py",
        "source_sha256": contract.base.files["sample.py"],
        "verification": {"after_sha256": contract.proposed.files["sample.py"]},
        "affected_workflows": ["classify", "route"],
        "unresolved_consumer_workflows": [],
        "scanner_warning_codes": [],
    }
    for coverage in ({"classify": ["application-smoke"]}, {"classify": [], "route": ["unknown"]}):
        with pytest.raises(ValueError, match="ALL_AFFECTED_WORKFLOWS"):
            review_change(artifact, contract, receipt, base, proposed, coverage)
    coverage = {key: ["application-smoke"] for key in artifact["affected_workflows"]}
    with pytest.raises(ValueError, match="UNRESOLVED_CONSUMERS"):
        review_change(
            {**artifact, "unresolved_consumer_workflows": ["dynamic-consumer"]},
            contract,
            receipt,
            base,
            proposed,
            coverage,
        )
    with pytest.raises(ValueError, match="BIND_EXACT_DIFF"):
        review_change(
            {**artifact, "source_sha256": "0" * 64}, contract, receipt, base, proposed, coverage
        )
    result = review_change(
        artifact, contract, receipt, base, proposed, coverage, branch="cutback/owned-review"
    )
    assert result["status"] == "READY_FOR_PR_REVIEW" and result["evidence_scope"] == "sample_only"
    assert (
        not result["remote_published"]
        and result["check_authenticity"] == "not_independently_attested"
    )
    assert result == review_change(
        artifact, contract, receipt, base, proposed, coverage, branch="cutback/owned-review"
    )
    assert not (proposed / "OWNED_HOOK_MUST_NOT_RUN").exists()
    assert (
        git(proposed, "rev-parse", "refs/heads/cutback/owned-review").decode().strip()
        == contract.proposed.commit
    )
    with pytest.raises(ValueError, match="BRANCH_INVALID"):
        review_change(artifact, contract, receipt, base, proposed, coverage, branch="--unsafe")
    other = (
        git(
            proposed,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit-tree",
            git(proposed, "rev-parse", "HEAD^{tree}").decode().strip(),
            "-p",
            contract.proposed.commit,
            "-m",
            "Another owned synthetic revision",
        )
        .decode()
        .strip()
    )
    git(proposed, "update-ref", "refs/heads/cutback/owned-review", other)
    with pytest.raises(ValueError, match="BRANCH_ALREADY_POINTS_ELSEWHERE"):
        review_change(
            artifact, contract, receipt, base, proposed, coverage, branch="cutback/owned-review"
        )
    assert git(proposed, "rev-parse", "refs/heads/cutback/owned-review").decode().strip() == other
