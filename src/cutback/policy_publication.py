"""One-file Git publication projection of a freshly verified source-policy review."""

from cutback.application_checks import ApplicationCheckContract
from cutback.core.request_profiles import fingerprint
from cutback.policy_changes import SourcePolicyProposal, review_change
from cutback.review_publication import _prepare_single_source


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
    spec = ApplicationCheckContract.model_validate(contract)
    bound = SourcePolicyProposal.model_validate(artifact["proposal"])
    current = review_change(
        artifact, spec, receipt, base, proposed, coverage, branch=branch, create_branch=False
    )
    if review != current:
        raise ValueError("REVIEW_PUBLICATION_CURRENT_POLICY_REVIEW_REQUIRED")
    if (
        current["status"] != "READY_FOR_PR_REVIEW"
        or current["evidence_scope"] != "heldout_review_required"
        or current["cache_runtime_required"] is not False
        or bound.proposed.cache_execution is not None
        or current["remote_published"] is not False
        or current["production_deployed"] is not False
    ):
        raise ValueError("REVIEW_PUBLICATION_POLICY_HELDOUT_REVIEW_REQUIRED")
    # These are verified transport projections, not a new campaign review or a
    # modified domain-review hash. The intent retains the actual policy review ID.
    fields = {**current, "base_commit": spec.base.commit, "proposed_commit": spec.proposed.commit}
    source = {
        "source_file": bound.binding.configuration_file,
        "source_sha256": bound.binding.configuration_sha256,
        "verification": {"after_sha256": artifact["proposed_sha256"]},
    }
    prepared = _prepare_single_source(
        fields,
        source,
        base,
        proposed,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
    )
    prepared["intent"].update(
        review_contract=current["version"],
        publishes_application_policy_text=True,
    )
    prepared["intent"]["intent_sha256"] = fingerprint(
        {key: value for key, value in prepared["intent"].items() if key != "intent_sha256"}
    )
    prepared["pull_request"]["title"] = "Review checked source-policy improvement"
    prepared["pull_request"]["body"] = (
        "The changed application policy file includes its instruction text. "
        "Publication uploads that checked file and one commit, with no local ancestors.\n\n"
        + prepared["pull_request"]["body"]
    )
    return prepared
