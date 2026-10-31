"""Exact model/output/settings diffs for the qualified corpus consumer. Never deploys."""

import difflib
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import fingerprint
from cutback.core.retrieval import RetrievalConfigurationBinding, RetrievalSettings
from cutback.core.retrieval_producer import RetrievalProducerBinding, qualify
from cutback.evals.experiments import ModelSpec, qualified_native_model
from cutback.retrieval_source import inspect_producer


class JointRetrievalProposal(StrictModel):
    version: Literal["cutback.joint-retrieval-proposal.v1"] = "cutback.joint-retrieval-proposal.v1"
    run_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: RetrievalConfigurationBinding
    producer: RetrievalProducerBinding
    baseline: ModelSpec
    proposed: ModelSpec
    proposed_settings: RetrievalSettings
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
            != (PurePosixPath(self.producer.file).parent / "retrieval.json").as_posix()
            or self.baseline.provider != self.producer.provider
            or self.baseline.model != self.producer.model
            or self.baseline.max_output_tokens != self.producer.max_output_tokens
            or self.baseline.instruction
            or self.baseline.request_contract
            or self.baseline.native_contract
            or self.baseline.exact_cache
            or self.baseline.routes
            or self.baseline.prompt_cache
            or self.proposed.model_dump(exclude={"key", "model", "max_output_tokens"})
            != self.baseline.model_dump(exclude={"key", "model", "max_output_tokens"})
            or self.proposed.max_output_tokens > self.baseline.max_output_tokens
            or self.proposed_settings.max_context_bytes > self.binding.settings.max_context_bytes
        ):
            raise ValueError("JOINT_RETRIEVAL_PROPOSAL_MUST_PRESERVE_QUALIFIED_PRODUCER")
        qualified_native_model(self.baseline)
        qualified_native_model(self.proposed)
        return self


def prepare_patch(root: Path, proposal: JointRetrievalProposal):
    settings, binding, producer = inspect_producer(root, proposal.producer.file)
    if binding != proposal.binding or producer != proposal.producer:
        raise ValueError("JOINT_RETRIEVAL_SOURCE_CHANGED_SINCE_APPROVED_PLAN")
    source = (root / producer.file).read_bytes()
    before_settings = (root / binding.file).read_bytes()
    if (
        hashlib.sha256(source).hexdigest() != producer.sha256
        or hashlib.sha256(before_settings).hexdigest() != binding.sha256
    ):
        raise ValueError("JOINT_RETRIEVAL_SOURCE_CHANGED_DURING_PATCH_PREPARATION")
    _, nodes = qualify(source)
    lines = source.splitlines(keepends=True)
    edits = []
    for key, value in {
        "model": proposal.proposed.model,
        "max_output_tokens": proposal.proposed.max_output_tokens,
    }.items():
        node = nodes[key]
        if node.value == value:
            continue
        start = sum(map(len, lines[: node.lineno - 1])) + node.col_offset
        end = sum(map(len, lines[: node.end_lineno - 1])) + node.end_col_offset
        edits.append((start, end, json.dumps(value).encode()))
    after_source = source
    for start, end, value in sorted(edits, reverse=True):
        after_source = after_source[:start] + value + after_source[end:]
    if qualify(after_source)[0] != (proposal.proposed.model, proposal.proposed.max_output_tokens):
        raise ValueError("JOINT_RETRIEVAL_LITERAL_PATCH_DID_NOT_BIND_PROPOSED_MODEL")
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
    changed = [(name, before, after) for name, before, after in changes if before != after]
    if not changed:
        raise ValueError("JOINT_RETRIEVAL_CHANGE_IS_EMPTY")
    if inspect_producer(root, producer.file) != (settings, binding, producer):
        raise ValueError("JOINT_RETRIEVAL_SOURCE_CHANGED_DURING_PATCH_PREPARATION")

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

    return {
        "version": "cutback.joint-retrieval-change.v1",
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
    from cutback.application_checks import ApplicationCheckContract, git, verify

    proposal = JointRetrievalProposal.model_validate(artifact["proposal"])
    if artifact != prepare_patch(base, proposal):
        raise ValueError("JOINT_RETRIEVAL_REVIEW_ARTIFACT_CHANGED")
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
        raise ValueError("JOINT_RETRIEVAL_CHECKS_DO_NOT_BIND_EXACT_COMBINED_CHANGE")
    commands = {command.key for command in contract.commands}
    if not isinstance(coverage, dict) or not 1 <= len(coverage) <= 32:
        raise ValueError("JOINT_RETRIEVAL_DECLARE_CONSUMER_CHECK_COVERAGE")
    for workflow, keys in coverage.items():
        TypeAdapter(Identifier).validate_python(workflow)
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 20
            or any(key not in commands for key in keys)
            or len(keys) != len(set(keys))
        ):
            raise ValueError("JOINT_RETRIEVAL_DECLARE_CONSUMER_CHECK_COVERAGE")
    ref = None
    if branch is not None:
        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("JOINT_RETRIEVAL_LOCAL_REVIEW_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        ref = "refs/heads/" + branch
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
        existing = dict(row.split(" ", 1) for row in rows.decode().splitlines()).get(ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("JOINT_RETRIEVAL_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            git(
                proposed,
                "update-ref",
                ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    result = {
        "version": "cutback.joint-retrieval-review.v1",
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
