"""Current common-policy authority over the shared private source/check runner."""

from pathlib import Path

from cutback.application_checks import git, run_checks
from cutback.application_handoff import HandoffEngine, rollback_check
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, write_private

CONTRACT = "cutback.source-policy-handoff.v1"


def retained_review(destination, artifact, contract, receipt, proposed, branch):
    path = destination / "review.json"
    if not path.is_file():
        raise ValueError("POLICY_HANDOFF_REVIEW_RECEIPT_MISSING_REFRESH_SAME_HANDOFF")
    value = read_private(path)
    proposal = artifact["proposal"]
    ref = "refs/heads/" + branch if branch else None
    if (
        not isinstance(value, dict)
        or value.get("version") != "cutback.source-policy-review.v1"
        or value.get("review_sha256")
        != fingerprint({key: item for key, item in value.items() if key != "review_sha256"})
        or any(
            value.get(key) != proposal[key]
            for key in ("program_id", "program_hash", "run_id", "plan_hash", "selection_hash")
        )
        or value.get("evidence_scope") != proposal["decision"]
        or value.get("base_tree_hash") != contract.base.tree_hash
        or value.get("proposed_tree_hash") != contract.proposed.tree_hash
        or value.get("application_contract_hash") != receipt["contract_hash"]
        or value.get("application_receipt_hash") != receipt["receipt_hash"]
        or value.get("local_review_ref") != ref
        or value.get("remote_published") is not False
        or value.get("production_deployed") is not False
    ):
        raise ValueError("POLICY_HANDOFF_REVIEW_RECEIPT_CHANGED")
    if ref:
        found = dict(
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
        if found != contract.proposed.commit:
            raise ValueError("POLICY_HANDOFF_REVIEW_REF_CHANGED")


def finish(
    root: Path,
    control,
    *,
    destination: Path,
    purpose,
    coverage=None,
    commands=None,
    execute_local=False,
    branch=None,
):
    from cutback.agent_policy_program import current, proposal, review
    from cutback.application_handoff import finish as shared_finish
    from cutback.policy_changes import SourcePolicyProposal, replacement_bytes

    def replacements(root, artifact):
        bound = SourcePolicyProposal.model_validate(artifact["proposal"])
        return {bound.binding.configuration_file: replacement_bytes(bound)}

    def checked_review(root, control, *, base, proposed, **options):
        return review(root, control, proposed_root=proposed, **options)

    def retained(destination, artifact, contract, receipt, base, proposed, branch, coverage):
        return retained_review(destination, artifact, contract, receipt, proposed, branch)

    engine = HandoffEngine(
        contract=CONTRACT,
        journal_prefix="policy-handoff-",
        identifier=lambda saved: saved["program_id"],
        identity=lambda saved, artifact: {
            "program_id": saved["program_id"],
            "program_hash": saved["program_hash"],
        },
        current=current,
        proposal=proposal,
        replacements=replacements,
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
        )
    except ValueError as failure:
        code = str(failure)
        if code.startswith("APPLICATION_HANDOFF_"):
            raise ValueError(
                "POLICY_HANDOFF_" + code.removeprefix("APPLICATION_HANDOFF_")
            ) from None
        raise
