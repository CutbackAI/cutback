"""Exact whole-policy configuration diffs and source-bound local review."""

import difflib
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, TypeAdapter, field_validator, model_validator

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_producer import PolicySourceBinding
from cutback.core.request_profiles import fingerprint
from cutback.policy_source import inspect_configuration
from cutback.source_policy import parse_policy


class SourcePolicyProposal(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.source-policy-proposal.v1"] = "cutback.source-policy-proposal.v1"
    program_id: UUID
    program_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    selection_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: PolicySourceBinding
    original: SourceImprovementPolicy
    proposed: SourceImprovementPolicy
    decision: Literal[
        "synthetic_local_review_only", "sample_review_required", "heldout_review_required"
    ]
    optimization_known_modeled_usd: Decimal = Field(ge=0, le=10000, decimal_places=12)
    production_approval_required: Literal[True] = True
    full_task_cost_known: Literal[False] = False

    @field_validator("optimization_known_modeled_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_SOURCE_EXACT_MODELED_COST_REQUIRED")
        return value

    @model_validator(mode="after")
    def source_frame(self):
        before, after = self.original, self.proposed
        if (
            not self.program_id.int
            or not self.run_id.int
            or before.policy_sha256 != self.binding.policy_sha256
            or before.workflow != self.binding.workflow
            or before.max_input_bytes != self.binding.max_input_bytes
            or after.workflow != before.workflow
            or after.max_input_bytes != before.max_input_bytes
            or after.baseline != before.baseline
            or after.max_instruction_bytes != before.max_instruction_bytes
            or after.runtime_cache_activation != before.runtime_cache_activation
            or after.behavior_sha256 == before.behavior_sha256
        ):
            raise ValueError("POLICY_SOURCE_PROPOSAL_FRAME_OR_BEHAVIOR_CHANGED")
        return self


def replacement_bytes(proposal: SourcePolicyProposal):
    value = proposal.proposed.configuration_bytes()
    if parse_policy(value) != proposal.proposed:
        raise ValueError("POLICY_SOURCE_SERIALIZED_CONFIGURATION_CHANGED")
    return value


def prepare_patch(root: Path, proposal: SourcePolicyProposal):
    original, binding = inspect_configuration(root, proposal.binding.producer_file)
    if binding != proposal.binding or original != proposal.original:
        raise ValueError("POLICY_SOURCE_CHANGED_SINCE_APPROVED_PROGRAM")
    with (root / binding.configuration_file).open("rb") as stream:
        before = stream.read(262145)
    if hashlib.sha256(before).hexdigest() != binding.configuration_sha256:
        raise ValueError("POLICY_SOURCE_CHANGED_DURING_PATCH")
    after = replacement_bytes(proposal)

    def diff(first, second, old, new):
        return "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in difflib.unified_diff(
                first.decode().splitlines(keepends=True),
                second.decode().splitlines(keepends=True),
                fromfile=old,
                tofile=new,
            )
        )

    return {
        "version": "cutback.source-policy-change.v1",
        "proposal": proposal.model_dump(mode="json"),
        "diff": diff(
            before, after, "a/" + binding.configuration_file, "b/" + binding.configuration_file
        ),
        "rollback_diff": diff(
            after, before, "a/" + binding.configuration_file, "b/" + binding.configuration_file
        ),
        "proposed_sha256": hashlib.sha256(after).hexdigest(),
        "applied": False,
        "production_deployed": False,
        "cache_runtime_required": proposal.proposed.cache_execution is not None,
        "cache_enabled_by_patch": False,
        "economics_scope": (
            "native_and_declared_cache_components_fit_hosting_and_full_task_cost_unknown"
        ),
    }


def review_change(
    artifact, contract, receipt, base: Path, proposed: Path, coverage, *, branch=None
):
    from cutback.application_checks import ApplicationCheckContract, git, verify

    proposal = SourcePolicyProposal.model_validate(artifact["proposal"])
    if artifact != prepare_patch(base, proposal):
        raise ValueError("POLICY_SOURCE_REVIEW_ARTIFACT_CHANGED")
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
        or contract.base.files.get(binding.configuration_file) != binding.configuration_sha256
        or contract.base.files.get(binding.producer_file) != binding.producer_sha256
        or contract.proposed.files.get(binding.configuration_file) != artifact["proposed_sha256"]
        or contract.proposed.files.get(binding.producer_file) != binding.producer_sha256
        or changed != {binding.configuration_file}
    ):
        raise ValueError("POLICY_CHECKS_DO_NOT_BIND_EXACT_CONFIGURATION_AND_PRODUCER")
    commands = {value.key for value in contract.commands}
    if (
        not isinstance(coverage, dict)
        or not 1 <= len(coverage) <= 32
        or any(
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 20
            or any(type(key) is not str or key not in commands for key in keys)
            or len(set(keys)) != len(keys)
            for keys in coverage.values()
        )
    ):
        raise ValueError("POLICY_DECLARE_CONSUMER_CHECK_COVERAGE")
    for workflow in coverage:
        TypeAdapter(Identifier).validate_python(workflow)
    ref = None
    if branch is not None:
        if type(branch) is not str or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("POLICY_LOCAL_REVIEW_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        ref = "refs/heads/" + branch
        existing = dict(
            line.split(" ", 1)
            for line in git(
                proposed,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                ref,
            )
            .decode()
            .splitlines()
        ).get(ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("POLICY_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            git(
                proposed,
                "update-ref",
                ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    result = {
        "version": "cutback.source-policy-review.v1",
        "status": "CACHE_RUNTIME_CONFIGURATION_REQUIRED"
        if proposal.proposed.cache_execution is not None
        else "READY_FOR_PR_REVIEW"
        if proposal.decision == "heldout_review_required"
        else "READY_FOR_LOCAL_REVIEW",
        "program_id": str(proposal.program_id),
        "program_hash": proposal.program_hash,
        "run_id": str(proposal.run_id),
        "plan_hash": proposal.plan_hash,
        "selection_hash": proposal.selection_hash,
        "evidence_scope": proposal.decision,
        "base_tree_hash": contract.base.tree_hash,
        "proposed_tree_hash": contract.proposed.tree_hash,
        "application_contract_hash": checked["contract_hash"],
        "application_receipt_hash": receipt["receipt_hash"],
        "declared_consumer_check_coverage": coverage,
        "coverage_trust": "owner_declared_not_all_consumers_discovered",
        "check_authenticity": checked["authenticity"],
        "local_review_ref": ref,
        "cache_runtime_required": proposal.proposed.cache_execution is not None,
        "cache_runtime_verified": False,
        "full_task_cost_known": False,
        "remote_published": False,
        "production_deployed": False,
    }
    return {**result, "review_sha256": fingerprint(result)}
