"""Exact one-file Git projection of a freshly verified native routing review."""

from cutback.application_checks import validate_contract
from cutback.core.request_profiles import fingerprint
from cutback.review_publication import _prepare_single_source
from cutback.routing_changes import RoutingSourceProposal, review_change


def prepare_publication(
    review,
    artifact,
    contract,
    receipt,
    base,
    proposed,
    coverage,
    *,
    branch,
    repository,
    base_branch,
    head_branch,
):
    spec = validate_contract(contract)
    bound = RoutingSourceProposal.model_validate(artifact["proposal"])
    current = review_change(
        artifact,
        spec,
        receipt,
        base,
        proposed,
        coverage,
        branch=branch,
        create_branch=False,
    )
    if review != current:
        raise ValueError("REVIEW_PUBLICATION_CURRENT_ROUTING_REVIEW_REQUIRED")
    if (
        current["status"] != "READY_FOR_PR_REVIEW"
        or current["evidence_scope"] != "heldout_review_required"
        or current["remote_published"] is not False
        or current["production_deployed"] is not False
    ):
        raise ValueError("REVIEW_PUBLICATION_ROUTING_HELDOUT_REVIEW_REQUIRED")
    prepared = _prepare_single_source(
        {**current, "base_commit": spec.base.commit, "proposed_commit": spec.proposed.commit},
        {
            "source_file": bound.binding.settings_file,
            "source_sha256": bound.binding.settings_sha256,
            "verification": {"after_sha256": artifact["proposed_sha256"]},
        },
        base,
        proposed,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
    )
    prepared["intent"].update(
        review_contract=current["version"],
        publishes_application_routing_settings=True,
        publishes_application_policy_text=True,
    )
    prepared["intent"]["intent_sha256"] = fingerprint(
        {key: value for key, value in prepared["intent"].items() if key != "intent_sha256"}
    )
    prepared["pull_request"]["title"] = "Review checked native routing improvement"
    prepared["pull_request"]["body"] = (
        "This draft uploads the checked application routing settings, including their "
        "instruction text, and one commit, "
        "without local ancestors. Native targets and byte-based routes preserve the "
        "frozen application input contract. Consumer check coverage is owner-declared; "
        "full task cost and deployment coverage remain unknown.\n\n"
        + prepared["pull_request"]["body"]
    )
    return prepared
