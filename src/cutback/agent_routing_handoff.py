"""Canonical routing authority over the shared private source/check handoff."""

from pathlib import Path

from cutback.application_checks import git, run_checks
from cutback.application_handoff import HandoffEngine, rollback_check
from cutback.core.prompts import fingerprint
from cutback.core.workspace import read_private, write_private

CONTRACT = "cutback.routing-program-handoff.v1"


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
    container=None,
):
    from cutback.agent_routing_program import current, proposal, review
    from cutback.application_handoff import finish as shared_finish
    from cutback.routing_changes import RoutingSourceProposal, replacement_bytes

    def replacements(root, artifact):
        bound = RoutingSourceProposal.model_validate(artifact["proposal"])
        return {bound.binding.settings_file: replacement_bytes(bound)}

    def checked_review(root, control, *, base, proposed, **options):
        return review(root, control, proposed_root=proposed, **options)

    def retained(destination, artifact, contract, receipt, base, proposed, branch, coverage):
        return retained_review(destination, artifact, contract, receipt, proposed, branch)

    engine = HandoffEngine(
        contract=CONTRACT,
        journal_prefix="routing-handoff-",
        identifier=lambda saved: saved["program_id"],
        identity=lambda saved, artifact: {
            "program_id": saved["program_id"],
            "program_hash": saved["program_hash"],
        },
        current=current,
        proposal=proposal,
        replacements=replacements,
        # The paired wrapper contract cannot enumerate all runtime consumers.
        required_workflows=lambda artifact: None,
        review=checked_review,
        retained_review=retained,
        write=write_private,
        checks=run_checks,
        rollback=rollback_check,
    )
    try:
        return shared_finish(
            root,
            control,
            engine=engine,
            destination=destination,
            purpose=purpose,
            coverage=coverage,
            commands=commands,
            execute_local=execute_local,
            branch=branch,
            container=container,
        )
    except ValueError as exc:
        code = str(exc)
        if code.startswith("APPLICATION_HANDOFF_"):
            raise ValueError(
                "ROUTING_HANDOFF_" + code.removeprefix("APPLICATION_HANDOFF_")
            ) from None
        raise
