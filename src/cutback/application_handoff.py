"""Prepare and execute a source-bound check handoff with durable identities.

Content/confirmation authority is always refreshed through the canonical engine.
Local checks need an explicit execution request and are never replayed after an
uncertain dispatch. This module does not publish, merge or deploy.
"""

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from cutback.application_checks import (
    ApplicationCheckContract,
    ApplicationCommand,
    git,
    run_checks,
    snapshot,
    source_root,
    validate_contract,
    validate_receipt,
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


def validate_coverage(coverage, commands, expected):
    if not isinstance(coverage, dict) or not 1 <= len(coverage) <= 32:
        raise ValueError("APPLICATION_HANDOFF_OWNER_CONSUMER_COVERAGE_REQUIRED")
    if expected is not None and (set(coverage) != set(expected) or not expected):
        raise ValueError("APPLICATION_HANDOFF_ALL_AFFECTED_CONSUMERS_REQUIRE_COVERAGE")
    keys = {command.key for command in commands}
    for workflow, selected in coverage.items():
        TypeAdapter(Identifier).validate_python(workflow)
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= 20
            or any(type(key) is not str or key not in keys for key in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("APPLICATION_HANDOFF_OWNER_CONSUMER_COVERAGE_REQUIRED")


def checked_paths(state, root, destination, expected, replacements):
    pair = destination / ("attempt-" + str(state["preparations"]))
    base, proposed = pair / "base", pair / "proposed"
    if snapshot(root) != expected or snapshot(base) != expected:
        raise ValueError("APPLICATION_HANDOFF_BASE_SOURCE_CHANGED")
    candidate = snapshot(proposed)
    files = {
        **expected.files,
        **{name: hashlib.sha256(raw).hexdigest() for name, raw in replacements.items()},
    }
    if candidate.files != files:
        raise ValueError("APPLICATION_HANDOFF_PROPOSED_SOURCE_CHANGED")
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
            raise ValueError("APPLICATION_HANDOFF_ROLLBACK_BYTES_CHANGED")
    git(proposed, "apply", "--check", str(patch))
    git(proposed, "apply", str(patch))
    if snapshot(root) != frozen:
        raise ValueError("APPLICATION_HANDOFF_ORIGINAL_SOURCE_CHANGED")


def retained_receipt(destination, contract, base, proposed):
    path = destination / "checks.json"
    if not path.is_file():
        raise ValueError("APPLICATION_HANDOFF_CHECK_REPLY_UNCERTAIN_NO_AUTOMATIC_REPLAY")
    receipt = read_private(path)
    validate_receipt(receipt)
    if (
        fingerprint({key: value for key, value in receipt.items() if key != "receipt_hash"})
        != receipt["receipt_hash"]
        or receipt["contract_hash"] != fingerprint(contract.model_dump(mode="json"))
        or receipt["base_tree_hash"] != contract.base.tree_hash
        or receipt["proposed_tree_hash"] != contract.proposed.tree_hash
    ):
        raise ValueError("APPLICATION_HANDOFF_CHECK_RECEIPT_CHANGED")
    if receipt["all_passed"]:
        verify(contract, receipt, base, proposed)
    return receipt


@dataclass(frozen=True)
class HandoffEngine:
    """Engine-specific canonical authority; source/check execution stays shared."""

    contract: str
    journal_prefix: str
    identifier: Callable[..., Any]
    identity: Callable[..., Any]
    current: Callable[..., Any]
    proposal: Callable[..., Any]
    replacements: Callable[..., Any]
    required_workflows: Callable[..., Any]
    review: Callable[..., Any]
    retained_review: Callable[..., Any]
    write: Callable[..., Any] = write_private
    checks: Callable[..., Any] = run_checks
    rollback: Callable[..., Any] = rollback_check


def finish(
    root: Path,
    control,
    *,
    engine: HandoffEngine,
    destination: Path,
    purpose: str,
    coverage=None,
    commands=None,
    execute_local=False,
    branch=None,
    container=None,
):
    from cutback.agent import workspace
    from cutback.application_check_discovery import discover
    from cutback.application_workspaces import materialize

    if type(execute_local) is not bool:
        raise ValueError("APPLICATION_HANDOFF_EXPLICIT_LOCAL_EXECUTION_REQUIRED")
    if container is not None:
        from cutback.application_container import ApplicationContainer

        container = ApplicationContainer.model_validate(container).model_dump(mode="json")
    # Validate the output boundary before requesting encrypted source content.
    root = source_root(root)
    destination = Path(os.path.abspath(destination))
    if destination.resolve().is_relative_to(root) or root.is_relative_to(destination.resolve()):
        raise ValueError("APPLICATION_HANDOFF_PRIVATE_WORKSPACE_MUST_BE_INDEPENDENT")
    prepare_private_output(destination.parent / ".cutback-handoff-path-check")
    if destination.is_symlink():
        raise ValueError("APPLICATION_HANDOFF_PRIVATE_WORKSPACE_REDIRECTED")
    root, directory = workspace(root)
    saved = engine.current(root, control)
    identifier = str(UUID(engine.identifier(saved)))
    journal = directory / (engine.journal_prefix + identifier + ".json")
    if destination.exists():
        if not journal.is_file():
            raise ValueError("APPLICATION_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
        prepare_private_output(destination / "contract.json")
    artifact = engine.proposal(root, control, purpose=purpose)
    expected = snapshot(root)
    replacements = engine.replacements(root, artifact)
    affected_workflows = engine.required_workflows(artifact)
    declared = (
        TypeAdapter(list[ApplicationCommand]).validate_python(commands)
        if commands is not None
        else None
    )
    identity = {
        "contract": engine.contract,
        **engine.identity(saved, artifact),
        "source_commit": expected.commit,
        "source_tree_hash": expected.tree_hash,
        "artifact_hash": fingerprint(artifact),
        "destination": str(destination),
        "commands": [value.model_dump(mode="json") for value in declared]
        if declared is not None
        else "discover_existing_full_suite",
        "branch": branch,
    }
    if container is not None:
        identity["container"] = container
    with workspace_lock(directory):
        if journal.exists():
            state = read_private(journal)
            if not isinstance(state, dict) or state.get("identity") != identity:
                raise ValueError("APPLICATION_HANDOFF_FROZEN_INTENT_CHANGED")
        else:
            if destination.exists():
                raise ValueError("APPLICATION_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
            state = {
                "identity": identity,
                "phase": "PREPARING",
                "preparations": 0,
                "workspace_nonce": str(uuid4()),
                "coverage": None,
            }
            engine.write(journal, state, exclusive=True)
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
            raise ValueError("APPLICATION_HANDOFF_JOURNAL_SHAPE_INVALID")
        destination.mkdir(exist_ok=True, mode=0o700)
        marker = destination / "intent.json"
        ownership = {
            "nonce": state.get("workspace_nonce"),
            "identity_sha256": fingerprint(identity),
        }
        if not isinstance(ownership["nonce"], str) or not UUID(ownership["nonce"]).int:
            raise ValueError("APPLICATION_HANDOFF_JOURNAL_SHAPE_INVALID")
        if marker.exists():
            if read_private(marker) != ownership:
                raise ValueError("APPLICATION_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
        else:
            if any(destination.iterdir()):
                raise ValueError("APPLICATION_HANDOFF_EXISTING_DESTINATION_NOT_OWNED")
            engine.write(marker, ownership, exclusive=True)
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
                        "APPLICATION_HANDOFF_PREPARATION_BOUND_REACHED_INSPECT_PRIVATE_FILES"
                    )
                state["preparations"] += 1
                engine.write(journal, state)
                pair = destination / ("attempt-" + str(state["preparations"]))
                materialize(root, pair, expected, replacements)
            base, proposed = checked_paths(state, root, destination, expected, replacements)
            engine.rollback(artifact, root, proposed, destination, expected)
            base, proposed = checked_paths(state, root, destination, expected, replacements)
            if declared is None:
                discovery = discover(base, proposed)
                contract = ApplicationCheckContract.model_validate(discovery["contract"])
                engine.write(destination / "discovery.json", discovery)
            else:
                contract = ApplicationCheckContract(
                    base=snapshot(base), proposed=snapshot(proposed), commands=declared
                )
            if container is not None:
                from cutback.application_container import isolate

                contract = isolate(contract, container)
            engine.write(destination / "proposal.json", artifact)
            engine.write(destination / "contract.json", contract.model_dump(mode="json"))
            state.update(
                phase="CHECKS_READY", contract_hash=fingerprint(contract.model_dump(mode="json"))
            )
            engine.write(journal, state)
        base, proposed = checked_paths(state, root, destination, expected, replacements)
        contract = validate_contract(read_private(destination / "contract.json"))
        if fingerprint(contract.model_dump(mode="json")) != state.get("contract_hash"):
            raise ValueError("APPLICATION_HANDOFF_CHECK_CONTRACT_CHANGED")
        if coverage is not None:
            validate_coverage(coverage, contract.commands, affected_workflows)
            if state.get("coverage") is not None and state["coverage"] != coverage:
                raise ValueError("APPLICATION_HANDOFF_FROZEN_COVERAGE_CHANGED")
            if state.get("coverage") is None:
                if state["phase"] != "CHECKS_READY":
                    raise ValueError("APPLICATION_HANDOFF_COVERAGE_REQUIRED_BEFORE_CHECKS")
                state["coverage"] = coverage
                engine.write(journal, state)
        coverage = state.get("coverage")
        if not execute_local:
            known_execution: bool | None = False
            if state["phase"] in {"CHECKS_PASSED", "CHECKS_FAILED", "REVIEWED"}:
                prior_receipt = retained_receipt(destination, contract, base, proposed)
                known_execution = True
                if state["phase"] == "REVIEWED":
                    engine.retained_review(
                        destination,
                        artifact,
                        contract,
                        prior_receipt,
                        base,
                        proposed,
                        branch,
                        coverage,
                    )
            elif state["phase"] == "CHECKS_DISPATCHED":
                known_execution = None
                if (destination / "checks.json").is_file():
                    retained_receipt(destination, contract, base, proposed)
                    known_execution = True
            return {
                "contract": engine.contract,
                "status": state["phase"],
                "contract_hash": state["contract_hash"],
                "workspace": str(destination),
                "base_root": str(base),
                "proposed_root": str(proposed),
                "command_keys": [command.key for command in contract.commands],
                "affected_workflows": sorted(affected_workflows)
                if affected_workflows is not None
                else None,
                "owner_consumer_coverage_required": coverage is None,
                "application_checks_executed": known_execution,
                "production_deployed": False,
            }
        validate_coverage(coverage, contract.commands, affected_workflows)
        receipt_file = destination / "checks.json"
        if state["phase"] in {"CHECKS_DISPATCHED", "CHECKS_PASSED", "REVIEWED"}:
            receipt = retained_receipt(destination, contract, base, proposed)
            verify(contract, receipt, base, proposed)
            should_run = False
        elif state["phase"] == "CHECKS_READY":
            state["phase"] = "CHECKS_DISPATCHED"
            engine.write(journal, state)
            should_run = True
        else:
            raise ValueError("APPLICATION_HANDOFF_FAILED_CHECKS_REQUIRE_EXPLICIT_NEW_SOURCE_REVIEW")
    if should_run:
        receipt = engine.checks(contract, base, proposed, execute_local=True)
        with workspace_lock(directory):
            engine.write(receipt_file, receipt)
            state["phase"] = "CHECKS_PASSED" if receipt["all_passed"] else "CHECKS_FAILED"
            engine.write(journal, state)
        if not receipt["all_passed"]:
            return {
                "contract": engine.contract,
                "status": "CHECKS_FAILED",
                "workspace": str(destination),
                "production_deployed": False,
            }
    # Refresh server confirmation/content authority after execution as well.
    checked_review = engine.review(
        root,
        control,
        purpose=purpose,
        contract=contract,
        receipt=receipt,
        base=base,
        proposed=proposed,
        coverage=coverage,
        branch=branch,
    )
    with workspace_lock(directory):
        engine.write(destination / "review.json", checked_review)
        state["phase"] = "REVIEWED"
        engine.write(journal, state)
    return {
        "contract": engine.contract,
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
