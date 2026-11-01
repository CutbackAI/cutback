"""Current campaign confirmation to private checked source trees and local review."""

import hashlib
from pathlib import Path

from cutback.application_checks import git, run_checks
from cutback.application_handoff import HandoffEngine, rollback_check
from cutback.core.prompts import fingerprint
from cutback.core.workspace import read_private, write_private

CONTRACT = "cutback.campaign-handoff.v1"


def retained_review(destination, artifact, contract, receipt, base, proposed, branch, coverage):
    from cutback.campaign_review import review_change

    path = destination / "review.json"
    if not path.is_file():
        raise ValueError("CAMPAIGN_HANDOFF_REVIEW_RECEIPT_MISSING_REFRESH_SAME_HANDOFF")
    # Reconstruct without branch creation: a preview cannot repair a missing ref.
    expected = review_change(artifact, contract, receipt, base, proposed, coverage)
    expected.pop("review_sha256")
    ref = "refs/heads/" + branch if branch else None
    expected["local_review_ref"] = ref
    expected["review_sha256"] = fingerprint(expected)
    if read_private(path) != expected:
        raise ValueError("CAMPAIGN_HANDOFF_REVIEW_RECEIPT_CHANGED")
    if ref:
        rows = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)", ref)
        found = dict(line.split(" ", 1) for line in rows.decode().splitlines()).get(ref)
        if found != contract.proposed.commit:
            raise ValueError("CAMPAIGN_HANDOFF_REVIEW_REF_CHANGED")


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
    sample_only=False,
):
    from cutback.agent_campaign import current, proposal, review
    from cutback.application_handoff import finish as shared_finish
    from cutback.application_patch import MAX_SOURCE_BYTES, replacement_bytes
    from cutback.core.request_profiles import safe_relative

    if type(sample_only) is not bool:
        raise ValueError("CAMPAIGN_HANDOFF_EXPLICIT_SAMPLE_SCOPE_REQUIRED")

    def proposed(root, control, *, purpose):
        return proposal(root, control, purpose=purpose, sample_only=sample_only)

    def replacements(root, artifact):
        safe_relative(artifact["source_file"])
        path = root / artifact["source_file"]
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError("CAMPAIGN_HANDOFF_SOURCE_BOUND_EXCEEDED")
        if hashlib.sha256(raw).hexdigest() != artifact["source_sha256"]:
            raise ValueError("CAMPAIGN_HANDOFF_SOURCE_CHANGED")
        return {artifact["source_file"]: replacement_bytes(raw, artifact)}

    def checked_review(root, control, **options):
        return review(root, control, sample_only=sample_only, **options)

    def identity(saved, artifact):
        if saved["campaign_id"] != artifact["campaign_id"]:
            raise ValueError("CAMPAIGN_HANDOFF_CURRENT_CAMPAIGN_CHANGED")
        return {
            "campaign_id": saved["campaign_id"],
            "campaign_hash": artifact["campaign_hash"],
            "project_id": saved["project_id"],
            "endpoint": saved["endpoint"],
            "sample_only": sample_only,
        }

    engine = HandoffEngine(
        contract=CONTRACT,
        journal_prefix="campaign-handoff-",
        identifier=lambda saved: saved["campaign_id"],
        identity=identity,
        current=current,
        proposal=proposed,
        replacements=replacements,
        required_workflows=lambda artifact: artifact["affected_workflows"],
        review=checked_review,
        retained_review=retained_review,
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
    except ValueError as exc:
        code = str(exc)
        if code.startswith("APPLICATION_HANDOFF_"):
            raise ValueError(
                "CAMPAIGN_HANDOFF_" + code.removeprefix("APPLICATION_HANDOFF_")
            ) from None
        raise
