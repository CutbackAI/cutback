"""Explicit local settings inspection and reviewable diffs, never source execution."""

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field

from cutback.core.models import StrictModel
from cutback.core.retrieval import CONTRACT_SHA256, RetrievalConfigurationBinding, RetrievalSettings


class RetrievalProposal(StrictModel):
    version: Literal["cutback.retrieval-proposal.v1"] = "cutback.retrieval-proposal.v1"
    run_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: RetrievalConfigurationBinding
    baseline: RetrievalSettings
    proposed: RetrievalSettings
    decision: Literal[
        "synthetic_local_review_only", "sample_review_required", "heldout_review_required"
    ]
    production_approval_required: Literal[True] = True


def _settings(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("RETRIEVAL_CONFIGURATION_DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    return RetrievalSettings.model_validate(
        json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    )


def inspect_configuration(root: Path, name: str):
    """Read the explicitly selected settings file in a clean, bounded Git snapshot."""
    from cutback.application_checks import snapshot, source_root
    from cutback.core.request_profiles import safe_relative

    safe_relative(name)
    root = source_root(root)
    tree = snapshot(root)
    path = root / name
    if name not in tree.files or not name.endswith(".json") or path.stat().st_size > 8192:
        raise ValueError("RETRIEVAL_CONFIGURATION_NOT_A_BOUNDED_TRACKED_JSON_FILE")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != tree.files[name]:
        raise ValueError("RETRIEVAL_SOURCE_CHANGED_DURING_INSPECTION")
    settings = _settings(raw)
    binding = RetrievalConfigurationBinding(
        settings=settings,
        retrieval_contract_sha256=CONTRACT_SHA256,
        file=name,
        sha256=hashlib.sha256(raw).hexdigest(),
        source_tree_hash=tree.tree_hash,
        commit=tree.commit,
    )
    return settings, binding


def prepare_patch(root: Path, proposal: RetrievalProposal):
    settings, binding = inspect_configuration(root, proposal.binding.file)
    if binding != proposal.binding or settings != proposal.baseline:
        raise ValueError("RETRIEVAL_SOURCE_CHANGED_SINCE_APPROVED_PLAN")
    if settings == proposal.proposed:
        raise ValueError("RETRIEVAL_CHANGE_IS_EMPTY")
    before = (root / binding.file).read_bytes()
    if hashlib.sha256(before).hexdigest() != binding.sha256:
        raise ValueError("RETRIEVAL_SOURCE_CHANGED_DURING_PATCH_PREPARATION")
    after = (json.dumps(proposal.proposed.model_dump(mode="json"), indent=2) + "\n").encode()

    # git apply cannot reliably represent a missing final newline without its marker.
    def diff(a, b, old, new):
        lines = difflib.unified_diff(
            a.decode().splitlines(keepends=True),
            b.decode().splitlines(keepends=True),
            fromfile=old,
            tofile=new,
        )
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in lines
        )

    return {
        "version": "cutback.retrieval-change.v1",
        "proposal": proposal.model_dump(mode="json"),
        "diff": diff(before, after, "a/" + binding.file, "b/" + binding.file),
        "rollback_diff": diff(after, before, "a/" + binding.file, "b/" + binding.file),
        "proposed_sha256": hashlib.sha256(after).hexdigest(),
        "review_status": "human_review_and_both_source_tree_checks_required",
        "application_coverage": "explicit_settings_file_only_not_all_consumers",
        "application_changed": False,
        "provider_calls_made": 0,
    }


def review_change(
    artifact, contract, receipt, base: Path, proposed: Path, coverage, *, branch=None
):
    """Bind exact settings to checked trees and declared consumers. Never pushes."""
    from pydantic import TypeAdapter

    from cutback.application_checks import ApplicationCheckContract, git, verify
    from cutback.core.models import Identifier
    from cutback.core.request_profiles import fingerprint

    proposal = RetrievalProposal.model_validate(artifact["proposal"])
    if artifact != prepare_patch(base, proposal):
        raise ValueError("RETRIEVAL_REVIEW_ARTIFACT_CHANGED")
    contract = ApplicationCheckContract.model_validate(contract)
    checked = verify(contract, receipt, base, proposed)
    binding = proposal.binding
    changed = {
        name
        for name in contract.base.files.keys() | contract.proposed.files.keys()
        if contract.base.files.get(name) != contract.proposed.files.get(name)
    }
    if (
        contract.base.commit != binding.commit
        or contract.base.tree_hash != binding.source_tree_hash
        or contract.base.files.get(binding.file) != binding.sha256
        or contract.proposed.files.get(binding.file) != artifact["proposed_sha256"]
        or changed != {binding.file}
    ):
        raise ValueError("RETRIEVAL_CHECKS_DO_NOT_BIND_EXACT_CONFIGURATION_CHANGE")
    commands = {c.key for c in contract.commands}
    if (
        not isinstance(coverage, dict)
        or not 1 <= len(coverage) <= 32
        or any(
            not isinstance(keys, list)
            or not keys
            or len(keys) > 20
            or any(k not in commands for k in keys)
            or len(set(keys)) != len(keys)
            for keys in coverage.values()
        )
    ):
        raise ValueError("RETRIEVAL_DECLARE_SETTINGS_CONSUMER_CHECK_COVERAGE")
    for workflow in coverage:
        TypeAdapter(Identifier).validate_python(workflow)
    ref = None
    if branch is not None:
        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("RETRIEVAL_LOCAL_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        ref = "refs/heads/" + branch
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
        existing = dict(row.split(" ", 1) for row in rows.decode().splitlines()).get(ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("RETRIEVAL_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            git(
                proposed,
                "update-ref",
                ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    result = {
        "version": "cutback.retrieval-application-review.v1",
        "status": "READY_FOR_PR_REVIEW"
        if proposal.decision == "heldout_review_required"
        else "READY_FOR_LOCAL_REVIEW",
        "run_id": str(proposal.run_id),
        "plan_hash": proposal.plan_hash,
        "evidence_scope": proposal.decision,
        "base_tree_hash": contract.base.tree_hash,
        "proposed_tree_hash": contract.proposed.tree_hash,
        "application_contract_hash": checked["contract_hash"],
        "application_receipt_hash": receipt["receipt_hash"],
        "declared_consumer_check_coverage": coverage,
        "coverage_trust": "owner_declared_not_all_consumers_discovered",
        "check_authenticity": checked["authenticity"],
        "local_review_ref": ref,
        "remote_published": False,
        "production_deployed": False,
    }
    return {**result, "review_sha256": fingerprint(result)}
