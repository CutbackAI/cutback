"""Exact combined model/cap/settings diffs for a qualified source consumer."""

import difflib
import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from cutback.compression_changes import inspect_configuration
from cutback.core.compression import CompressionConfigurationBinding, CompressionSettings
from cutback.core.context_producer import ContextProducerBinding, qualify
from cutback.core.models import StrictModel
from cutback.evals.experiments import ModelSpec, qualified_native_model


def inspect_producer(root: Path, name: str):
    from cutback.application_checks import source_root
    from cutback.core.request_profiles import safe_relative

    safe_relative(name)
    root = source_root(root)
    settings, binding = inspect_configuration(
        root, str(Path(name).parent / "compression.json").replace("\\", "/")
    )
    path = root / name
    if (
        path.is_symlink()
        or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root))
        or not path.resolve(strict=True).is_relative_to(root)
        or not path.is_file()
        or path.stat().st_size > 65536
    ):
        raise ValueError("JOINT_SOURCE_PRODUCER_NOT_BOUNDED_FILE")
    raw = path.read_bytes()
    values, _ = qualify(raw)
    from cutback.application_checks import snapshot

    tree = snapshot(root)
    hashed = hashlib.sha256(raw).hexdigest()
    if tree.tree_hash != binding.source_tree_hash or tree.files.get(name) != hashed:
        raise ValueError("JOINT_SOURCE_CHANGED_DURING_INSPECTION")
    producer = ContextProducerBinding(
        file=name,
        sha256=hashed,
        source_tree_hash=tree.tree_hash,
        commit=tree.commit,
        model=values[0],
        max_output_tokens=values[1],
    )
    return settings, binding, producer


class JointContextProposal(StrictModel):
    version: Literal["cutback.joint-context-proposal.v1"] = "cutback.joint-context-proposal.v1"
    run_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: CompressionConfigurationBinding
    producer: ContextProducerBinding
    baseline: ModelSpec
    proposed: ModelSpec
    proposed_settings: CompressionSettings
    decision: Literal[
        "synthetic_local_review_only", "sample_review_required", "heldout_review_required"
    ]
    production_approval_required: Literal[True] = True

    @model_validator(mode="after")
    def exact(self):
        if (
            self.producer.commit != self.binding.commit
            or self.producer.source_tree_hash != self.binding.source_tree_hash
            or self.binding.file
            != str(Path(self.producer.file).parent / "compression.json").replace("\\", "/")
            or self.baseline.provider != self.producer.provider
            or self.baseline.model != self.producer.model
            or self.baseline.max_output_tokens != self.producer.max_output_tokens
            or self.baseline.instruction
            or self.baseline.request_contract
            or self.baseline.native_contract
            or self.proposed.model_dump(exclude={"key", "model", "max_output_tokens"})
            != self.baseline.model_dump(exclude={"key", "model", "max_output_tokens"})
            or self.proposed.max_output_tokens > self.baseline.max_output_tokens
            or self.proposed_settings.max_context_bytes > self.binding.settings.max_context_bytes
        ):
            raise ValueError("JOINT_PROPOSAL_MUST_PRESERVE_QUALIFIED_PRODUCER_CONTRACT")
        qualified_native_model(self.baseline)
        qualified_native_model(self.proposed)
        return self


def prepare_patch(root: Path, proposal: JointContextProposal):
    settings, binding, producer = inspect_producer(root, proposal.producer.file)
    if binding != proposal.binding or producer != proposal.producer:
        raise ValueError("JOINT_SOURCE_CHANGED_SINCE_APPROVED_PLAN")
    source = (root / producer.file).read_bytes()
    if hashlib.sha256(source).hexdigest() != producer.sha256:
        raise ValueError("JOINT_SOURCE_CHANGED_DURING_PATCH_PREPARATION")
    _, nodes = qualify(source)
    lines = source.splitlines(keepends=True)
    edits = []
    for key, value in {
        "model": proposal.proposed.model,
        "max_output_tokens": proposal.proposed.max_output_tokens,
    }.items():
        node = nodes[key]
        start = sum(map(len, lines[: node.lineno - 1])) + node.col_offset
        end = sum(map(len, lines[: node.end_lineno - 1])) + node.end_col_offset
        edits.append((start, end, json.dumps(value).encode()))
    after_source = source
    for start, end, replacement in sorted(edits, reverse=True):
        after_source = after_source[:start] + replacement + after_source[end:]
    values, _ = qualify(after_source)
    if values != (proposal.proposed.model, proposal.proposed.max_output_tokens):
        raise ValueError("JOINT_LITERAL_PATCH_DID_NOT_BIND_PROPOSED_MODEL")
    before_settings = (root / binding.file).read_bytes()
    if hashlib.sha256(before_settings).hexdigest() != binding.sha256:
        raise ValueError("JOINT_SETTINGS_CHANGED_DURING_PATCH_PREPARATION")
    after_settings = (
        before_settings
        if settings == proposal.proposed_settings
        else (
            json.dumps(proposal.proposed_settings.model_dump(mode="json"), indent=2) + "\n"
        ).encode()
    )
    changes = [
        (producer.file, source, after_source),
        (binding.file, before_settings, after_settings),
    ]

    def diff(name, before, after):
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in difflib.unified_diff(
                before.decode().splitlines(keepends=True),
                after.decode().splitlines(keepends=True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )

    changed = [(name, before, after) for name, before, after in changes if before != after]
    if not changed:
        raise ValueError("JOINT_CHANGE_IS_EMPTY")
    return {
        "version": "cutback.joint-context-change.v1",
        "proposal": proposal.model_dump(mode="json"),
        "diff": "".join(diff(name, before, after) for name, before, after in changed),
        "rollback_diff": "".join(
            diff(name, after, before) for name, before, after in reversed(changed)
        ),
        "proposed_files": {name: hashlib.sha256(after).hexdigest() for name, _, after in changed},
        "application_coverage": "qualified_exact_wrapper_and_owner_declared_consumer_checks",
        "review_status": "human_review_and_both_source_tree_checks_required",
        "application_changed": False,
        "provider_calls_made": 0,
    }


def review_change(
    artifact, contract, receipt, base: Path, proposed: Path, coverage, *, branch=None
):
    from pydantic import TypeAdapter

    from cutback.application_checks import ApplicationCheckContract, git, verify
    from cutback.core.models import Identifier
    from cutback.core.request_profiles import fingerprint

    proposal = JointContextProposal.model_validate(artifact["proposal"])
    if artifact != prepare_patch(base, proposal):
        raise ValueError("JOINT_REVIEW_ARTIFACT_CHANGED")
    contract = ApplicationCheckContract.model_validate(contract)
    checked = verify(contract, receipt, base, proposed)
    changed = {
        name
        for name in contract.base.files.keys() | contract.proposed.files.keys()
        if contract.base.files.get(name) != contract.proposed.files.get(name)
    }
    if (
        contract.base.commit != proposal.binding.commit
        or contract.base.tree_hash != proposal.binding.source_tree_hash
        or changed != set(artifact["proposed_files"])
        or any(
            contract.proposed.files.get(name) != value
            for name, value in artifact["proposed_files"].items()
        )
    ):
        raise ValueError("JOINT_CHECKS_DO_NOT_BIND_EXACT_COMBINED_CHANGE")
    commands = {command.key for command in contract.commands}
    if not isinstance(coverage, dict) or not 1 <= len(coverage) <= 32:
        raise ValueError("JOINT_DECLARE_CONSUMER_CHECK_COVERAGE")
    for workflow, keys in coverage.items():
        TypeAdapter(Identifier).validate_python(workflow)
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 20
            or any(key not in commands for key in keys)
            or len(keys) != len(set(keys))
        ):
            raise ValueError("JOINT_DECLARE_CONSUMER_CHECK_COVERAGE")
    ref = None
    if branch is not None:
        import re

        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("JOINT_LOCAL_REVIEW_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        ref = "refs/heads/" + branch
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
        existing = dict(row.split(" ", 1) for row in rows.decode().splitlines()).get(ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("JOINT_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            git(
                proposed,
                "update-ref",
                ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    result = {
        "version": "cutback.joint-context-review.v1",
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
        "coverage_trust": "owner_declared_not_all_callers_discovered",
        "check_authenticity": checked["authenticity"],
        "local_review_ref": ref,
        "remote_published": False,
        "production_deployed": False,
    }
    return {**result, "review_sha256": fingerprint(result)}
