"""Bind a confirmed proposal to portable application checks and a local review ref."""

import hashlib
import re
from pathlib import Path

from cutback.application_checks import git, validate_contract, verify
from cutback.core.prompts import fingerprint


def review_change(
    artifact, contract, receipt, base: Path, proposed: Path, coverage, *, branch=None
):
    """Verify existing checks; never execute application code, push or deploy.

    Coverage is an explicit owner assertion mapping affected workflow IDs to
    command keys. A passing command does not independently establish that it
    exercises that workflow or represents customer quality.
    """
    contract = validate_contract(contract)
    if (
        artifact.get("status") != "DRY_RUN_ONLY"
        or artifact.get("production_deployed") is not False
        or artifact.get("confirmation_gate", {}).get("passed") is not True
        or artifact.get("confirmation_gate", {}).get("plan_hash") != artifact["plan_hash"]
        or hashlib.sha256(artifact["diff"].encode()).hexdigest() != artifact["diff_sha256"]
    ):
        raise ValueError("CAMPAIGN_EXACT_REVIEWABLE_PROPOSAL_REQUIRED")
    result = verify(contract, receipt, base, proposed)
    source = artifact["source_file"]
    if (
        contract.base.files.get(source) != artifact["source_sha256"]
        or contract.proposed.files.get(source) != artifact["verification"]["after_sha256"]
        or artifact.get("source_commit")
        and contract.base.commit != artifact["source_commit"]
    ):
        raise ValueError("CAMPAIGN_APPLICATION_CHECKS_DO_NOT_BIND_EXACT_DIFF")
    changed = {
        name
        for name in contract.base.files.keys() | contract.proposed.files.keys()
        if contract.base.files.get(name) != contract.proposed.files.get(name)
    }
    if changed != {source}:
        raise ValueError("CAMPAIGN_REVIEW_CONTAINS_UNEVALUATED_FILE_CHANGES")
    if (
        artifact.get("unresolved_consumer_workflows") != []
        or artifact.get("scanner_warning_codes") != []
    ):
        raise ValueError("CAMPAIGN_UNRESOLVED_CONSUMERS_REQUIRE_SEPARATE_REVIEW")
    affected = set(artifact["affected_workflows"])
    commands = {command.key for command in contract.commands}
    if (
        not affected
        or not isinstance(coverage, dict)
        or set(coverage) != affected
        or any(
            not isinstance(keys, list)
            or not keys
            or any(not isinstance(key, str) or key not in commands for key in keys)
            or len(set(keys)) != len(keys)
            for keys in coverage.values()
        )
    ):
        raise ValueError("CAMPAIGN_ALL_AFFECTED_WORKFLOWS_REQUIRE_DECLARED_CHECKS")
    review_ref = None
    if branch is not None:
        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("CAMPAIGN_LOCAL_REVIEW_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        review_ref = "refs/heads/" + branch
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", review_ref)
        existing = dict(row.split(" ", 1) for row in rows.decode().splitlines()).get(review_ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("CAMPAIGN_LOCAL_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            git(
                proposed,
                "update-ref",
                review_ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    value = {
        "contract": "cutback.campaign-application-review.v1",
        "status": "READY_FOR_PR_REVIEW",
        "campaign_id": artifact["campaign_id"],
        "campaign_hash": artifact["campaign_hash"],
        "run_id": artifact["run_id"],
        "plan_hash": artifact["plan_hash"],
        "diff_sha256": artifact["diff_sha256"],
        "rollback_diff_sha256": hashlib.sha256(artifact["rollback_diff"].encode()).hexdigest(),
        "base_commit": contract.base.commit,
        "proposed_commit": contract.proposed.commit,
        "base_tree_hash": contract.base.tree_hash,
        "proposed_tree_hash": contract.proposed.tree_hash,
        "application_contract_hash": result["contract_hash"],
        "application_receipt_hash": receipt["receipt_hash"],
        "declared_workflow_check_coverage": coverage,
        "coverage_trust": "owner_declared_not_independently_attested",
        "consumer_scope": "scanner_detected_source_only_not_runtime_or_external_consumers",
        "check_authenticity": result["authenticity"],
        "evidence_scope": artifact["confirmation_gate"]["scope"],
        "local_review_ref": review_ref,
        "remote_published": False,
        "production_deployed": False,
    }
    return {**value, "review_sha256": fingerprint(value)}
