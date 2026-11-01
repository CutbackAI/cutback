"""Prepare and execute one source-bound routing check handoff with durable identities.

Content/confirmation authority is always refreshed through the ordinary routing
program. Local checks need an explicit execution request and are never replayed
after an uncertain dispatch. This module does not publish, merge or deploy.
"""

import hashlib
import os
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from cutback.application_checks import (
    ApplicationCheckContract,
    ApplicationCheckReceipt,
    ApplicationCommand,
    git,
    run_checks,
    snapshot,
    source_root,
    verify,
)
from cutback.core.models import Identifier
from cutback.core.prompts import fingerprint
from cutback.core.workspace import (
    prepare_private_output,
    read_private,
    workspace_lock,
    write_private,
    write_private_text_output,
)

CONTRACT = "cutback.routing-program-handoff.v1"


def validate_coverage(coverage, commands):
    if not isinstance(coverage, dict) or not 1 <= len(coverage) <= 32:
        raise ValueError("ROUTING_HANDOFF_OWNER_CONSUMER_COVERAGE_REQUIRED")
    keys = {command.key for command in commands}
    for workflow, selected in coverage.items():
        TypeAdapter(Identifier).validate_python(workflow)
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= 20
            or any(type(key) is not str or key not in keys for key in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("ROUTING_HANDOFF_OWNER_CONSUMER_COVERAGE_REQUIRED")


def checked_paths(state, root, destination, expected, replacements):
    pair = destination / ("attempt-" + str(state["preparations"]))
    base, proposed = pair / "base", pair / "proposed"
    if snapshot(root) != expected or snapshot(base) != expected:
        raise ValueError("ROUTING_HANDOFF_BASE_SOURCE_CHANGED")
    candidate = snapshot(proposed)
    files = {
        **expected.files,
        **{name: hashlib.sha256(raw).hexdigest() for name, raw in replacements.items()},
    }
    if candidate.files != files:
        raise ValueError("ROUTING_HANDOFF_PROPOSED_SOURCE_CHANGED")
    return base, proposed


def rollback_check(artifact, root, proposed, destination, frozen):
    """Apply the actual inverse/forward patches only in the owned proposed tree."""
    patch, rollback = destination / "change.patch", destination / "rollback.patch"
    write_private_text_output(patch, artifact["diff"])
    write_private_text_output(rollback, artifact["rollback_diff"])
    git(proposed, "apply", "--check", str(rollback))
    git(proposed, "apply", str(rollback))
    for name, digest in frozen.files.items():
        path = proposed / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("ROUTING_HANDOFF_ROLLBACK_BYTES_CHANGED")
    git(proposed, "apply", "--check", str(patch))
    git(proposed, "apply", str(patch))
    if snapshot(root) != frozen:
        raise ValueError("ROUTING_HANDOFF_ORIGINAL_SOURCE_CHANGED")


def retained_receipt(destination, contract, base, proposed):
    path = destination / "checks.json"
    if not path.is_file():
        raise ValueError("ROUTING_HANDOFF_CHECK_REPLY_UNCERTAIN_NO_AUTOMATIC_REPLAY")
    receipt = read_private(path)
    ApplicationCheckReceipt.model_validate(receipt)
    if (
        fingerprint({key: value for key, value in receipt.items() if key != "receipt_hash"})
        != receipt["receipt_hash"]
        or receipt["contract_hash"] != fingerprint(contract.model_dump(mode="json"))
        or receipt["base_tree_hash"] != contract.base.tree_hash
        or receipt["proposed_tree_hash"] != contract.proposed.tree_hash
    ):
        raise ValueError("ROUTING_HANDOFF_CHECK_RECEIPT_CHANGED")
    if receipt["all_passed"]:
        verify(contract, receipt, base, proposed)
    return receipt


def retained_review(destination, artifact, contract, receipt, proposed, branch):
    path = destination / "review.json"
    if not path.is_file():
        raise ValueError("ROUTING_HANDOFF_REVIEW_RECEIPT_MISSING_REFRESH_SAME_HANDOFF")
    value = read_private(path)
    ref = "refs/heads/" + branch if branch else None
    if (
        not isinstance(value, dict)
        or value.get("version") != "cutback.routing-source-review.v1"
        or value.get("review_sha256")
        != fingerprint({key: item for key, item in value.items() if key != "review_sha256"})
        or value.get("run_id") != artifact["proposal"]["run_id"]
        or value.get("plan_hash") != artifact["proposal"]["plan_hash"]
        or value.get("evidence_scope") != artifact["proposal"]["decision"]
        or value.get("base_tree_hash") != contract.base.tree_hash
        or value.get("proposed_tree_hash") != contract.proposed.tree_hash
        or value.get("application_contract_hash") != receipt["contract_hash"]
        or value.get("application_receipt_hash") != receipt["receipt_hash"]
        or value.get("local_review_ref") != ref
        or value.get("remote_published") is not False
        or value.get("production_deployed") is not False
    ):
        raise ValueError("ROUTING_HANDOFF_REVIEW_RECEIPT_CHANGED")
    if ref:
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
        found = dict(line.split(" ", 1) for line in rows.decode().splitlines()).get(ref)
        if found != contract.proposed.commit:
            raise ValueError("ROUTING_HANDOFF_REVIEW_REF_CHANGED")


def finish(
    root: Path,
    control,
    *,
    destination: Path,
    purpose: str,
    coverage=None,
    commands=None,
    execute_local=False,
    branch=None,
):
    from cutback.agent import workspace
    from cutback.agent_routing_program import current, proposal, review
    from cutback.application_check_discovery import discover
    from cutback.application_workspaces import materialize
    from cutback.routing_changes import RoutingSourceProposal, replacement_bytes

    if type(execute_local) is not bool:
        raise ValueError("ROUTING_HANDOFF_EXPLICIT_LOCAL_EXECUTION_REQUIRED")
    # Validate the output boundary before requesting encrypted source content.
    root = source_root(root)
    destination = Path(os.path.abspath(destination))
    if destination.resolve().is_relative_to(root) or root.is_relative_to(destination.resolve()):
        raise ValueError("ROUTING_HANDOFF_PRIVATE_WORKSPACE_MUST_BE_INDEPENDENT")
    prepare_private_output(destination.parent / ".cutback-handoff-path-check")
    if destination.is_symlink():
        raise ValueError("ROUTING_HANDOFF_PRIVATE_WORKSPACE_REDIRECTED")
    root, directory = workspace(root)
    saved = current(root, control)
    identifier = str(UUID(saved["program_id"]))
    journal = directory / ("routing-handoff-" + identifier + ".json")
    if destination.exists():
        if not journal.is_file():
            raise ValueError("ROUTING_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
        prepare_private_output(destination / "contract.json")
    artifact = proposal(root, control, purpose=purpose)
    bound = RoutingSourceProposal.model_validate(artifact["proposal"])
    expected = snapshot(root)
    replacements = {bound.binding.settings_file: replacement_bytes(bound)}
    declared = (
        TypeAdapter(list[ApplicationCommand]).validate_python(commands)
        if commands is not None
        else None
    )
    identity = {
        "contract": CONTRACT,
        "program_id": identifier,
        "program_hash": saved["program_hash"],
        "source_commit": expected.commit,
        "source_tree_hash": expected.tree_hash,
        "artifact_hash": fingerprint(artifact),
        "destination": str(destination),
        "commands": [value.model_dump(mode="json") for value in declared]
        if declared is not None
        else "discover_existing_full_suite",
        "branch": branch,
    }
    with workspace_lock(directory):
        if journal.exists():
            state = read_private(journal)
            if not isinstance(state, dict) or state.get("identity") != identity:
                raise ValueError("ROUTING_HANDOFF_FROZEN_INTENT_CHANGED")
        else:
            if destination.exists():
                raise ValueError("ROUTING_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
            state = {
                "identity": identity,
                "phase": "PREPARING",
                "preparations": 0,
                "workspace_nonce": str(uuid4()),
                "coverage": None,
            }
            write_private(journal, state, exclusive=True)
        if (
            type(state.get("preparations")) is not int
            or not 0 <= state["preparations"] <= 3
            or type(state.get("phase")) is not str
            or state.get("phase")
            not in {
                "PREPARING",
                "CHECKS_READY",
                "CHECKS_DISPATCHED",
                "CHECKS_PASSED",
                "CHECKS_FAILED",
                "REVIEWED",
            }
        ):
            raise ValueError("ROUTING_HANDOFF_JOURNAL_SHAPE_INVALID")
        destination.mkdir(exist_ok=True, mode=0o700)
        marker = destination / "intent.json"
        ownership = {
            "nonce": state.get("workspace_nonce"),
            "identity_sha256": fingerprint(identity),
        }
        if not isinstance(ownership["nonce"], str) or not UUID(ownership["nonce"]).int:
            raise ValueError("ROUTING_HANDOFF_JOURNAL_SHAPE_INVALID")
        if marker.exists():
            if read_private(marker) != ownership:
                raise ValueError("ROUTING_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
        else:
            if any(destination.iterdir()):
                raise ValueError("ROUTING_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
            write_private(marker, ownership, exclusive=True)
        if state["phase"] == "PREPARING":
            pair = destination / ("attempt-" + str(state["preparations"]))
            complete = False
            if state["preparations"] and (pair / "workspaces.json").is_file():
                try:
                    prepared = read_private(pair / "workspaces.json")
                    complete = (
                        isinstance(prepared, dict) and prepared.get("status") == "WORKSPACES_READY"
                    )
                    if complete:
                        checked_paths(state, root, destination, expected, replacements)
                except (ValueError, OSError):
                    # A crash may interrupt the private inverse/forward check.
                    # No app check has been dispatched in PREPARING. Preserve
                    # the incomplete copy and use one bounded fresh attempt.
                    complete = False
            if not complete:
                if state["preparations"] >= 3:
                    raise ValueError(
                        "ROUTING_HANDOFF_PREPARATION_BOUND_REACHED_INSPECT_PRIVATE_FILES"
                    )
                state["preparations"] += 1
                write_private(journal, state)
                pair = destination / ("attempt-" + str(state["preparations"]))
                materialize(root, pair, expected, replacements)
            base, proposed = checked_paths(state, root, destination, expected, replacements)
            rollback_check(artifact, root, proposed, destination, expected)
            base, proposed = checked_paths(state, root, destination, expected, replacements)
            if declared is None:
                discovery = discover(base, proposed)
                contract = ApplicationCheckContract.model_validate(discovery["contract"])
                write_private(destination / "discovery.json", discovery)
            else:
                contract = ApplicationCheckContract(
                    base=snapshot(base), proposed=snapshot(proposed), commands=declared
                )
            write_private(destination / "proposal.json", artifact)
            write_private(destination / "contract.json", contract.model_dump(mode="json"))
            state.update(
                phase="CHECKS_READY", contract_hash=fingerprint(contract.model_dump(mode="json"))
            )
            write_private(journal, state)
        base, proposed = checked_paths(state, root, destination, expected, replacements)
        contract = ApplicationCheckContract.model_validate(
            read_private(destination / "contract.json")
        )
        if fingerprint(contract.model_dump(mode="json")) != state.get("contract_hash"):
            raise ValueError("ROUTING_HANDOFF_CHECK_CONTRACT_CHANGED")
        if coverage is not None:
            validate_coverage(coverage, contract.commands)
            if state.get("coverage") is not None and state["coverage"] != coverage:
                raise ValueError("ROUTING_HANDOFF_FROZEN_COVERAGE_CHANGED")
            if state.get("coverage") is None:
                if state["phase"] != "CHECKS_READY":
                    raise ValueError("ROUTING_HANDOFF_COVERAGE_REQUIRED_BEFORE_CHECKS")
                state["coverage"] = coverage
                write_private(journal, state)
        coverage = state.get("coverage")
        if not execute_local:
            known_execution: bool | None = False
            if state["phase"] in {"CHECKS_PASSED", "CHECKS_FAILED", "REVIEWED"}:
                prior_receipt = retained_receipt(destination, contract, base, proposed)
                known_execution = True
                if state["phase"] == "REVIEWED":
                    retained_review(
                        destination, artifact, contract, prior_receipt, proposed, branch
                    )
            elif state["phase"] == "CHECKS_DISPATCHED":
                known_execution = None
                if (destination / "checks.json").is_file():
                    retained_receipt(destination, contract, base, proposed)
                    known_execution = True
            return {
                "contract": CONTRACT,
                "status": state["phase"],
                "contract_hash": state["contract_hash"],
                "workspace": str(destination),
                "base_root": str(base),
                "proposed_root": str(proposed),
                "command_keys": [command.key for command in contract.commands],
                "owner_consumer_coverage_required": coverage is None,
                "application_checks_executed": known_execution,
                "production_deployed": False,
            }
        validate_coverage(coverage, contract.commands)
        receipt_file = destination / "checks.json"
        if state["phase"] in {"CHECKS_DISPATCHED", "CHECKS_PASSED", "REVIEWED"}:
            receipt = retained_receipt(destination, contract, base, proposed)
            verify(contract, receipt, base, proposed)
            should_run = False
        elif state["phase"] == "CHECKS_READY":
            state["phase"] = "CHECKS_DISPATCHED"
            write_private(journal, state)
            should_run = True
        else:
            raise ValueError("ROUTING_HANDOFF_FAILED_CHECKS_REQUIRE_EXPLICIT_NEW_SOURCE_REVIEW")
    if should_run:
        receipt = run_checks(contract, base, proposed, execute_local=True)
        with workspace_lock(directory):
            write_private(receipt_file, receipt)
            state["phase"] = "CHECKS_PASSED" if receipt["all_passed"] else "CHECKS_FAILED"
            write_private(journal, state)
        if not receipt["all_passed"]:
            return {
                "contract": CONTRACT,
                "status": "CHECKS_FAILED",
                "workspace": str(destination),
                "production_deployed": False,
            }
    # Refresh server confirmation/content authority after execution as well.
    checked_review = review(
        root,
        control,
        purpose=purpose,
        contract=contract,
        receipt=receipt,
        proposed_root=proposed,
        coverage=coverage,
        branch=branch,
    )
    with workspace_lock(directory):
        write_private(destination / "review.json", checked_review)
        state["phase"] = "REVIEWED"
        write_private(journal, state)
    return {
        "contract": CONTRACT,
        "status": "REVIEWED",
        "workspace": str(destination),
        "base_root": str(base),
        "proposed_root": str(proposed),
        "local_review_ref": checked_review["local_review_ref"],
        "both_application_trees_checked": True,
        "byte_exact_rollback_checked": True,
        "remote_published": False,
        "production_deployed": False,
    }
