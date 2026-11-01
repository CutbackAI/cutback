"""Exact native policy inspection, reversible configuration changes and local review."""

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import fingerprint, safe_relative
from cutback.core.routing_producer import RoutingSourceBinding, qualify
from cutback.evals.experiments import ModelSpec, RouteSpec, provider_models, qualified_native_model


def policy_bytes(raw):
    """Configuration is data; reject ambiguous keys and unsupported native frames."""
    if len(raw) > 262144:
        raise ValueError("ROUTING_SOURCE_CONFIGURATION_BYTE_BOUND")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ROUTING_SOURCE_DUPLICATE_CONFIGURATION_KEY")
            result[key] = value
        return result

    try:
        policy = ModelSpec.model_validate(json.loads(raw.decode("utf-8"), object_pairs_hook=unique))
        if policy.provider != "router":
            raise ValueError
        targets = provider_models(policy)
        if not 1 <= len(targets) <= 8:
            raise ValueError
        for target in targets:
            if (
                target.provider != "openai"
                or target.request_contract
                or target.native_contract
                or target.prompt_cache
                or target.agent_turn
            ):
                raise ValueError
            qualified_native_model(target)
        expected = ModelSpec(
            key=policy.key, provider="router", model=policy.model, routes=policy.routes
        )
        if expected.model_dump(mode="json") != policy.model_dump(mode="json"):
            raise ValueError
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("ROUTING_SOURCE_CONFIGURATION_NOT_QUALIFIED") from None
    return policy


def inspect_configuration(root: Path, producer_file: str):
    """Read two tracked files from one clean Git snapshot without running either."""
    from cutback.application_checks import snapshot, source_root

    safe_relative(producer_file)
    root = source_root(root)
    tree = snapshot(root)
    settings_file = str(Path(producer_file).parent / "routing.json").replace("\\", "/")
    blobs = []
    for name, bound in [(producer_file, 65536), (settings_file, 262144)]:
        path = root / name
        if name not in tree.files or path.stat().st_size > bound:
            raise ValueError("ROUTING_SOURCE_REQUIRES_BOUNDED_TRACKED_PRODUCER_AND_POLICY")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != tree.files[name]:
            raise ValueError("ROUTING_SOURCE_CHANGED_DURING_INSPECTION")
        blobs.append(raw)
    qualify(blobs[0])
    policy = policy_bytes(blobs[1])
    if len(policy.routes) != 1:
        raise ValueError("ROUTING_SOURCE_TRAINING_REQUIRES_SINGLE_NATIVE_BASELINE")
    baseline = policy.routes[0].target
    binding = RoutingSourceBinding(
        producer_file=producer_file,
        producer_sha256=tree.files[producer_file],
        settings_file=settings_file,
        settings_sha256=tree.files[settings_file],
        source_tree_hash=tree.tree_hash,
        commit=tree.commit,
        configuration_key=policy.key,
        configuration_model=policy.model,
        policy_sha256=fingerprint(policy.model_dump(mode="json")),
        baseline_sha256=fingerprint(baseline.model_dump(mode="json")),
        max_input_bytes=policy.routes[0].max_input_bytes,
    )
    return baseline, binding


def validate_source_models(binding: RoutingSourceBinding, baseline: ModelSpec, candidates):
    if (
        baseline.provider != "openai"
        or fingerprint(baseline.model_dump(mode="json")) != binding.baseline_sha256
        or baseline.request_contract
        or baseline.native_contract
        or baseline.prompt_cache
        or baseline.agent_turn
    ):
        raise ValueError("ROUTING_SOURCE_BASELINE_OR_NATIVE_FRAME_CHANGED")
    configured = ModelSpec(
        key=binding.configuration_key,
        provider="router",
        model=binding.configuration_model,
        routes=[RouteSpec(max_input_bytes=binding.max_input_bytes, target=baseline)],
    )
    provider_models(configured)
    if fingerprint(configured.model_dump(mode="json")) != binding.policy_sha256:
        raise ValueError("ROUTING_SOURCE_BASELINE_CONFIGURATION_CHANGED")
    fixed = baseline.model_dump(mode="json", exclude={"key", "model", "max_output_tokens"})
    for candidate in candidates:
        for target in provider_models(candidate):
            if (
                target.model_dump(mode="json", exclude={"key", "model", "max_output_tokens"})
                != fixed
                or target.max_output_tokens > baseline.max_output_tokens
            ):
                raise ValueError("ROUTING_SOURCE_TARGET_MUST_PRESERVE_FRAME_AND_OUTPUT_BOUND")
            qualified_native_model(target)


class RoutingSourceProposal(StrictModel):
    version: Literal["cutback.routing-source-proposal.v1"] = "cutback.routing-source-proposal.v1"
    run_id: UUID
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    binding: RoutingSourceBinding
    baseline: ModelSpec
    proposed: ModelSpec
    decision: Literal[
        "synthetic_local_review_only", "sample_review_required", "heldout_review_required"
    ]
    production_approval_required: Literal[True] = True
    full_task_cost_known: Literal[False] = False

    @model_validator(mode="after")
    def exact(self):
        validate_source_models(self.binding, self.baseline, [self.proposed])
        if (
            self.proposed.provider != "router"
            or self.proposed.routes[-1].max_input_bytes != self.binding.max_input_bytes
            or any(
                route.max_input_bytes > self.binding.max_input_bytes
                for route in self.proposed.routes
            )
        ):
            raise ValueError("ROUTING_SOURCE_POLICY_MUST_PRESERVE_APPLICATION_INPUT_BOUND")
        return self


def replacement_bytes(proposal: RoutingSourceProposal):
    """Exact serialized policy bytes shared by patch preview and private materialization."""
    binding = proposal.binding
    policy = proposal.proposed.model_copy(
        update={
            "key": binding.configuration_key,
            "model": binding.configuration_model,
        }
    )
    # Revalidate the actual serialized file, including outer router defaults.
    policy_bytes(json.dumps(policy.model_dump(mode="json")).encode())
    if fingerprint(policy.model_dump(mode="json")) == binding.policy_sha256:
        raise ValueError("ROUTING_SOURCE_CHANGE_IS_EMPTY")
    return (json.dumps(policy.model_dump(mode="json"), indent=2) + "\n").encode()


def prepare_patch(root: Path, proposal: RoutingSourceProposal):
    baseline, binding = inspect_configuration(root, proposal.binding.producer_file)
    if binding != proposal.binding or baseline != proposal.baseline:
        raise ValueError("ROUTING_SOURCE_CHANGED_SINCE_APPROVED_PLAN")
    before = (root / binding.settings_file).read_bytes()
    if hashlib.sha256(before).hexdigest() != binding.settings_sha256:
        raise ValueError("ROUTING_SOURCE_CHANGED_DURING_PATCH")
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
        "version": "cutback.routing-source-change.v1",
        "proposal": proposal.model_dump(mode="json"),
        "diff": diff(before, after, "a/" + binding.settings_file, "b/" + binding.settings_file),
        "rollback_diff": diff(
            after, before, "a/" + binding.settings_file, "b/" + binding.settings_file
        ),
        "proposed_sha256": hashlib.sha256(after).hexdigest(),
        "applied": False,
        "production_deployed": False,
        "economics_scope": "native_components_only_fitting_hosting_and_full_task_cost_unknown",
    }


def review_change(
    artifact,
    contract,
    receipt,
    base: Path,
    proposed: Path,
    coverage,
    *,
    branch=None,
    create_branch=True,
):
    """Verify both actual Git trees and recorded checks; create only an optional local ref."""
    from cutback.application_checks import git, validate_contract, verify

    if type(create_branch) is not bool:
        raise ValueError("ROUTING_REVIEW_BRANCH_CREATION_REQUIRES_BOOLEAN")
    proposal = RoutingSourceProposal.model_validate(artifact["proposal"])
    if artifact != prepare_patch(base, proposal):
        raise ValueError("ROUTING_SOURCE_REVIEW_ARTIFACT_CHANGED")
    contract = validate_contract(contract)
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
        or contract.base.files.get(binding.settings_file) != binding.settings_sha256
        or contract.base.files.get(binding.producer_file) != binding.producer_sha256
        or contract.proposed.files.get(binding.settings_file) != artifact["proposed_sha256"]
        or contract.proposed.files.get(binding.producer_file) != binding.producer_sha256
        or changed != {binding.settings_file}
    ):
        raise ValueError("ROUTING_CHECKS_DO_NOT_BIND_EXACT_POLICY_AND_PRODUCER")
    commands = {command.key for command in contract.commands}
    if (
        not isinstance(coverage, dict)
        or not 1 <= len(coverage) <= 32
        or any(
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 20
            or any(not isinstance(key, str) for key in keys)
            or len(set(keys)) != len(keys)
            or any(key not in commands for key in keys)
            for keys in coverage.values()
        )
    ):
        raise ValueError("ROUTING_DECLARE_POLICY_CONSUMER_CHECK_COVERAGE")
    for workflow in coverage:
        TypeAdapter(Identifier).validate_python(workflow)
    ref = None
    if branch is not None:
        if not isinstance(branch, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", branch
        ):
            raise ValueError("ROUTING_LOCAL_REVIEW_BRANCH_INVALID")
        git(proposed, "check-ref-format", "--branch", branch)
        ref = "refs/heads/" + branch
        existing = dict(
            row.split(" ", 1)
            for row in git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
            .decode()
            .splitlines()
        ).get(ref)
        if existing and existing != contract.proposed.commit:
            raise ValueError("ROUTING_REVIEW_BRANCH_ALREADY_POINTS_ELSEWHERE")
        if not existing:
            if not create_branch:
                raise ValueError("ROUTING_REVIEW_BRANCH_MISSING")
            git(
                proposed,
                "update-ref",
                ref,
                contract.proposed.commit,
                "0" * len(contract.proposed.commit),
            )
    result = {
        "version": "cutback.routing-source-review.v1",
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
        "full_task_cost_known": False,
        "remote_published": False,
        "production_deployed": False,
    }
    return {**result, "review_sha256": fingerprint(result)}
