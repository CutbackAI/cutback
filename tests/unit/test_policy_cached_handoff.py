"""A cache configuration is not activation permission; active scopes require separate revision."""

import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from support_policy_application import cached_application

import cutback
from cutback.application_checks import ApplicationCommand, git, prepare, run_checks
from cutback.policy_changes import SourcePolicyProposal, prepare_patch, review_change
from cutback.policy_publication import prepare_publication
from cutback.policy_source import inspect_configuration


def proposal(root, *, requested):
    original, binding = inspect_configuration(root, "app.py")
    candidate = original.model_copy(deep=True)
    candidate.strategy.max_output_tokens = 32
    return SourcePolicyProposal(
        version="cutback.source-policy-proposal.v2",
        program_id=uuid4(),
        program_hash="a" * 64,
        run_id=uuid4(),
        plan_hash="b" * 64,
        dataset_hash="c" * 64,
        selection_hash="d" * 64,
        binding=binding,
        original=original,
        proposed=candidate,
        original_cache_requested=requested,
        proposed_cache_requested=requested,
        decision="heldout_review_required",
        optimization_known_modeled_usd="0.001",
    )


@pytest.mark.parametrize("requested", [False, True])
def test_checks_and_publication_respect_observed_activation_without_reading_private_scope(
    tmp_path, requested, monkeypatch
):
    base, _ = cached_application(tmp_path)
    bound = proposal(base, requested=requested)
    artifact = prepare_patch(base, bound)
    assert artifact["cache_runtime_required"] is requested
    assert artifact["cache_enabled_by_patch"] is False
    assert artifact["cache_scope_revision_required"] is requested
    assert artifact["runtime_activation_change_authorized"] is False
    monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", "OWNED_PRIVATE_SCOPE_MUST_NOT_BE_READ")
    proposed = tmp_path / "checked-independent-cached-policy"
    shutil.copytree(base, proposed)
    (proposed / "policy.json").write_bytes(bound.proposed.configuration_bytes())
    git(proposed, "add", "policy.json")
    git(
        proposed,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent cached-policy review fixture",
    )
    command = ApplicationCommand(
        key="native-contract",
        arguments=[
            "-c",
            "import sys,unittest; sys.path.insert(0, "
            + repr(str(Path(cutback.__file__).resolve().parents[1]))
            + "); unittest.main(module='test_app')",
        ],
    )
    contract = prepare(base, proposed, [command])
    receipt = run_checks(contract, base, proposed, execute_local=True)
    assert receipt["all_passed"]
    coverage = {bound.binding.workflow: [command.key]}
    branch = "cutback/owned-inactive-cache-review"
    review = review_change(artifact, contract, receipt, base, proposed, coverage, branch=branch)
    assert review["version"] == "cutback.source-policy-review.v2"
    assert review["observed_current_cache_requested"] is requested
    assert review["cache_runtime_verified"] is False
    assert review["status"] == (
        "CACHE_RUNTIME_CONFIGURATION_REQUIRED" if requested else "READY_FOR_PR_REVIEW"
    )
    options = dict(
        branch=branch,
        repository="example-owner/independent-policy",
        base_branch="main",
        head_branch=branch,
    )
    if requested:
        with pytest.raises(ValueError, match="HELDOUT_REVIEW_REQUIRED"):
            prepare_publication(
                review, artifact, contract, receipt, base, proposed, coverage, **options
            )
    else:
        # An owner-declared gate exercises transport only. This synthetic unit
        # provides no collector/customer quality claim and performs no upload.
        publication = prepare_publication(
            review, artifact, contract, receipt, base, proposed, coverage, **options
        )
        assert publication["intent"]["review_contract"] == "cutback.source-policy-review.v2"
        assert publication["intent"]["publishes_local_ancestors"] is False


def test_inactive_reference_cannot_be_silently_activated_and_legacy_wire_remains_unchanged(
    tmp_path,
):
    root, _ = cached_application(tmp_path)
    bound = proposal(root, requested=False)
    raw = bound.model_dump(mode="json")
    with pytest.raises(ValueError, match="FRAME_OR_BEHAVIOR_CHANGED"):
        SourcePolicyProposal.model_validate({**raw, "proposed_cache_requested": True})
    with pytest.raises(ValueError, match="FRAME_OR_BEHAVIOR_CHANGED"):
        SourcePolicyProposal.model_validate({**raw, "version": "cutback.source-policy-proposal.v1"})
    raw.pop("original_cache_requested")
    raw.pop("proposed_cache_requested")
    raw["version"] = "cutback.source-policy-proposal.v1"
    legacy = SourcePolicyProposal.model_validate(raw)
    assert legacy.model_dump(mode="json") == raw and legacy.cache_runtime_required is True
    active = bound.model_copy(update={"original_cache_requested": True})
    assert active.cache_runtime_required is True
    assert active.cache_review_fields()["cache_runtime_deactivation_required"] is True
