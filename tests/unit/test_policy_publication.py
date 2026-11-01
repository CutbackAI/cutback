"""Actual native app checks and Git object hashing; no vendor or GitHub network I/O."""

import copy
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from support_policy_application import application
from support_review_publication import OwnedGitHub, api_for, journal_for

import cutback
from cutback.application_checks import ApplicationCommand, git, prepare, run_checks
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, write_private
from cutback.policy_changes import SourcePolicyProposal, prepare_patch, review_change
from cutback.policy_publication import prepare_publication
from cutback.policy_source import inspect_configuration
from cutback.review_publication import publish_prepared


@pytest.fixture
def checked_policy(tmp_path):
    base = application(tmp_path)
    original, binding = inspect_configuration(base, "app.py")
    candidate = original.model_copy(deep=True)
    candidate.strategy = candidate.strategy.model_copy(update={"max_output_tokens": 32})
    # This fixture declares a gate solely to exercise the publication contract.
    # No collector-produced/customer held-out quality claim is made by this unit.
    bound = SourcePolicyProposal(
        program_id=uuid4(),
        program_hash="a" * 64,
        run_id=uuid4(),
        plan_hash="b" * 64,
        dataset_hash="c" * 64,
        selection_hash="d" * 64,
        binding=binding,
        original=original,
        proposed=candidate,
        decision="heldout_review_required",
        optimization_known_modeled_usd="0.001",
    )
    artifact = prepare_patch(base, bound)
    proposed = tmp_path / "checked-policy-proposal"
    shutil.copytree(base, proposed)
    (proposed / "policy.json").write_bytes(candidate.configuration_bytes())
    git(proposed, "add", "policy.json")
    git(
        proposed,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent checked policy proposal",
    )
    command = ApplicationCommand(
        key="native-policy-contract",
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
    coverage = {binding.workflow: [command.key]}
    branch = "cutback/checked-policy-review"
    review = review_change(artifact, contract, receipt, base, proposed, coverage, branch=branch)
    options = {
        "branch": branch,
        "repository": "example-owner/policy-app",
        "base_branch": "main",
        "head_branch": branch,
    }
    result = prepare_publication(
        review, artifact, contract, receipt, base, proposed, coverage, **options
    )
    entries = {}
    for raw in git(base, "ls-tree", "-z", "HEAD").split(b"\0"):
        if raw:
            header, name = raw.split(b"\t", 1)
            mode, kind, blob = header.decode().split()
            assert kind == "blob"
            entries[name.decode()] = (mode, blob)
    return base, proposed, artifact, contract, receipt, coverage, review, options, result, entries


def test_checked_policy_publishes_exact_objects_as_a_draft_without_local_ancestors(checked_policy):
    base, proposed, artifact, contract, receipt, coverage, review, options, prepared, entries = (
        checked_policy
    )
    assert prepared["intent"]["review_contract"] == "cutback.source-policy-review.v1"
    assert prepared["intent"]["publishes_application_policy_text"] is True
    assert prepared["intent"]["publishes_local_ancestors"] is False
    assert prepared["intent"]["source_files"] == ["policy.json"]
    assert prepared["intent"]["review_sha256"] == review["review_sha256"]
    remote = OwnedGitHub(prepared, base_entries=entries)
    journal, saves = journal_for(prepared), []
    api = api_for(remote)
    try:
        result = publish_prepared(
            prepared, api, journal=journal, save=lambda value: saves.append(copy.deepcopy(value))
        )
        assert result["draft"] and result["merge_authorized"] is False
        assert result["production_deployed"] is False
        assert result["proposed_commit"] == contract.proposed.commit
        assert remote.posts == ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
        assert publish_prepared(prepared, api, journal=journal, save=lambda _: None) == result
        assert len(remote.posts) == 5
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(saves)
        assert "synthetic-github-test-token" not in json.dumps(saves)
    finally:
        api.close()


@pytest.mark.parametrize("decision", ["synthetic_local_review_only", "sample_review_required"])
def test_sample_or_synthetic_policy_gate_cannot_be_published_as_heldout(checked_policy, decision):
    base, proposed, artifact, contract, receipt, coverage, _, options, _, _ = checked_policy
    changed = copy.deepcopy(artifact)
    changed["proposal"]["decision"] = decision
    review = review_change(
        changed, contract, receipt, base, proposed, coverage, branch=options["branch"]
    )
    with pytest.raises(ValueError, match="POLICY_HELDOUT_REVIEW_REQUIRED"):
        prepare_publication(review, changed, contract, receipt, base, proposed, coverage, **options)


def test_primary_policy_consumer_cannot_be_omitted_from_declared_check_coverage(checked_policy):
    base, proposed, artifact, contract, receipt, _, _, options, _, _ = checked_policy
    with pytest.raises(ValueError, match="DECLARE_CONSUMER_CHECK_COVERAGE"):
        review_change(
            artifact,
            contract,
            receipt,
            base,
            proposed,
            {"different-workflow": ["native-policy-contract"]},
            branch=options["branch"],
        )


def test_publication_preview_cannot_recreate_a_removed_local_review_ref(checked_policy):
    base, proposed, artifact, contract, receipt, coverage, review, options, _, _ = checked_policy
    git(proposed, "update-ref", "-d", "refs/heads/" + options["branch"])
    before = git(proposed, "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(ValueError, match="POLICY_REVIEW_BRANCH_MISSING"):
        prepare_publication(
            review, artifact, contract, receipt, base, proposed, coverage, **options
        )
    assert git(proposed, "for-each-ref", "--format=%(refname) %(objectname)") == before


def test_modified_review_hash_or_policy_diff_is_refused(checked_policy):
    base, proposed, artifact, contract, receipt, coverage, review, options, _, _ = checked_policy
    changed = {**review, "review_sha256": "0" * 64}
    with pytest.raises(ValueError, match="CURRENT_POLICY_REVIEW_REQUIRED"):
        prepare_publication(
            changed, artifact, contract, receipt, base, proposed, coverage, **options
        )
    artifact = {**artifact, "diff": artifact["diff"] + "\n"}
    with pytest.raises(ValueError, match="ARTIFACT_CHANGED"):
        prepare_publication(
            review, artifact, contract, receipt, base, proposed, coverage, **options
        )


def test_agent_preview_and_explicit_publication_refresh_scope_and_keep_journal_content_free(
    checked_policy, monkeypatch
):
    import cutback.agent_policy_program as program
    from cutback.agent import workspace
    from cutback.agent_policy_publication import publish_review

    base, proposed, artifact, contract, receipt, coverage, review, options, prepared, entries = (
        checked_policy
    )
    root, directory = workspace(base)
    saved = {
        "program_id": artifact["proposal"]["program_id"],
        "program_hash": artifact["proposal"]["program_hash"],
    }
    inputs = {
        "version": "cutback.source-policy-review-inputs.v1",
        **saved,
        "review_sha256": review["review_sha256"],
        "base": str(root),
        "proposed": str(proposed),
        "contract": contract.model_dump(mode="json"),
        "receipt": receipt,
        "coverage": coverage,
        "branch": options["branch"],
    }
    write_private(directory / "policy-program-review-inputs.json", inputs)
    write_private(directory / "policy-program-review.json", review)
    monkeypatch.setattr(program, "current", lambda *args: saved)
    refreshed = []

    def refresh(*args, **kwargs):
        refreshed.append(kwargs["purpose"])
        return artifact

    monkeypatch.setattr(program, "proposal", refresh)
    remote = OwnedGitHub(prepared, base_entries=entries)
    api = api_for(remote)
    args = {key: value for key, value in options.items() if key != "branch"}
    try:
        preview = publish_review(root, object(), purpose="owned-policy-preview", **args)
        assert preview["status"] == "PREVIEW_ONLY_EXPLICIT_PUBLICATION_REQUIRED"
        assert preview["intent_sha256"] == prepared["intent"]["intent_sha256"]
        assert remote.requests == []
        assert not (directory / "policy-program-publication.json").exists()
        result = publish_review(
            root, object(), purpose="owned-policy-publication", publish=True, api=api, **args
        )
        assert result["remote_published"] is True and result["draft"] is True
        assert len(remote.posts) == 5 and len(refreshed) == 2
        stored = read_private(directory / "policy-program-publication.json")
        assert stored["result"] == result
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in json.dumps(stored)
        assert "synthetic-github-test-token" not in json.dumps(stored)
        assert (
            publish_review(root, object(), purpose="owned-reconcile", publish=True, api=api, **args)
            == result
        )
        assert len(remote.posts) == 5 and len(refreshed) == 3

        def revoke(*args, **kwargs):
            raise ValueError("CURRENT_CONFIRMED_SOURCE_CONTENT_REQUIRED")

        monkeypatch.setattr(program, "proposal", revoke)
        before = list(remote.requests)
        with pytest.raises(ValueError, match="CURRENT_CONFIRMED_SOURCE_CONTENT_REQUIRED"):
            publish_review(root, object(), purpose="owned-revoked", publish=True, api=api, **args)
        assert remote.requests == before
    finally:
        api.close()


def test_review_writer_preserves_the_exact_hashed_receipt_wire_values(checked_policy, monkeypatch):
    import cutback.agent_policy_program as program
    from cutback.agent import workspace

    base, proposed, artifact, contract, receipt, coverage, review, options, _, _ = checked_policy
    saved = {
        "program_id": artifact["proposal"]["program_id"],
        "program_hash": artifact["proposal"]["program_hash"],
    }
    monkeypatch.setattr(program, "current", lambda *args: saved)
    monkeypatch.setattr(program, "proposal", lambda *args, **kwargs: artifact)
    result = program.review(
        base,
        object(),
        purpose="owned-wire-receipt-review",
        contract=contract,
        receipt=receipt,
        proposed_root=proposed,
        coverage=coverage,
        branch=options["branch"],
    )
    assert result == review
    _, directory = workspace(base)
    retained = read_private(directory / "policy-program-review-inputs.json")["receipt"]
    assert retained == receipt and retained["recorded_at"] == receipt["recorded_at"]
    assert (
        fingerprint({key: value for key, value in retained.items() if key != "receipt_hash"})
        == (retained["receipt_hash"])
    )


@pytest.mark.parametrize("malformed", [None, [], "OWNED_PRIVATE_REVIEW_ITEM"])
def test_malformed_local_review_stops_with_a_content_free_error_before_github(
    tmp_path, monkeypatch, malformed
):
    import cutback.agent_policy_program as program
    from cutback.agent import workspace
    from cutback.agent_policy_publication import publish_review

    root, directory = workspace(application(tmp_path))
    saved = {"program_id": str(uuid4()), "program_hash": "a" * 64}
    monkeypatch.setattr(program, "current", lambda *args: saved)
    monkeypatch.setattr(program, "proposal", lambda *args, **kwargs: {})
    write_private(
        directory / "policy-program-review-inputs.json",
        {
            "version": "cutback.source-policy-review-inputs.v1",
            **saved,
            "base": str(root),
            "review_sha256": "b" * 64,
        },
    )
    write_private(directory / "policy-program-review.json", malformed)
    with pytest.raises(ValueError, match="CURRENT_POLICY_REVIEW_REQUIRED"):
        publish_review(
            root,
            object(),
            purpose="owned-malformed-review",
            repository="example-owner/policy-app",
            base_branch="main",
            head_branch="cutback/checked-policy",
            publish=True,
            api=lambda *args: pytest.fail("MALFORMED_REVIEW_MUST_NOT_CONTACT_GITHUB"),
        )
    assert not (directory / "policy-program-publication.json").exists()
