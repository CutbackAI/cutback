"""Native static/learned checks, content boundaries and one-shot Git publication."""

import copy
import json
import shutil
from uuid import uuid4

import pytest
from support_application_container import container_image as container_image
from support_review_publication import OwnedGitHub, api_for, journal_for
from support_routing_publication import checked_routing

from cutback.application_checks import git
from cutback.core.workspace import read_private, write_private
from cutback.review_publication import publish_prepared
from cutback.routing_changes import review_change
from cutback.routing_publication import prepare_publication


@pytest.fixture(scope="module", params=[False, True], ids=["static-control", "learned-routes"])
def checked_template(tmp_path_factory, request):
    # Execute the actual native consumer on each exact base/proposed pair once.
    # Network journal cases reuse those portable recorded receipts while every
    # publication rechecks both independent copied trees and the receipt hash.
    return checked_routing(tmp_path_factory.mktemp("routing-native-checked"), learned=request.param)


@pytest.fixture
def checked(tmp_path, checked_template):
    value = copy.deepcopy(checked_template)
    for key in ("base", "proposed"):
        path = tmp_path / key
        shutil.copytree(value[key], path)
        value[key] = path
    return value


def prepare(value, **changes):
    return prepare_publication(
        value["review"],
        value["artifact"],
        value["contract"],
        value["receipt"],
        value["base"],
        value["proposed"],
        value["coverage"],
        **{**value["options"], **changes},
    )


def test_routing_draft_uploads_only_checked_settings_and_reconciles(checked):
    prepared = prepare(checked)
    assert prepared["intent"]["review_contract"] == "cutback.routing-source-review.v1"
    assert prepared["intent"]["source_files"] == ["routing.json"]
    assert prepared["intent"]["publishes_local_ancestors"] is False
    assert prepared["intent"]["publishes_application_policy_text"] is True
    remote, journal = OwnedGitHub(prepared, base_entries=checked["entries"]), journal_for(prepared)
    api = api_for(remote)
    try:
        result = publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert result["draft"] and result["merge_authorized"] is False
        assert result["proposed_commit"] == checked["contract"].proposed.commit
        assert publish_prepared(prepared, api, journal=journal, save=lambda _: None) == result
        assert remote.posts == ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
        assert checked["artifact"]["proposal"]["baseline"]["instruction"] not in json.dumps(journal)
        assert "synthetic-github-test-token" not in json.dumps(journal)
    finally:
        api.close()


@pytest.mark.parametrize(
    "lost", ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
)
def test_routing_lost_acknowledgement_reconciles_without_repeated_mutation(checked, lost):
    prepared = prepare(checked)
    remote = OwnedGitHub(prepared, lose=lost, base_entries=checked["entries"])
    api, journal = api_for(remote), journal_for(prepared)
    try:
        with pytest.raises(ValueError, match="REMOTE_OUTCOME_UNCERTAIN"):
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        result = publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert result["draft"] and len(remote.pulls) == 1 and remote.posts.count(lost) == 1
    finally:
        api.close()


@pytest.mark.parametrize("decision", ["synthetic_local_review_only", "sample_review_required"])
def test_routing_synthetic_and_sample_reviews_cannot_be_published_as_heldout(checked, decision):
    changed = copy.deepcopy(checked["artifact"])
    changed["proposal"]["decision"] = decision
    reviewed = review_change(
        changed,
        checked["contract"],
        checked["receipt"],
        checked["base"],
        checked["proposed"],
        checked["coverage"],
        branch=checked["options"]["branch"],
    )
    with pytest.raises(ValueError, match="ROUTING_HELDOUT_REVIEW_REQUIRED"):
        prepare_publication(
            reviewed,
            changed,
            checked["contract"],
            checked["receipt"],
            checked["base"],
            checked["proposed"],
            checked["coverage"],
            **checked["options"],
        )


def test_routing_publication_never_recreates_deleted_local_review_ref(checked):
    git(checked["proposed"], "update-ref", "-d", "refs/heads/" + checked["options"]["branch"])
    before = git(checked["proposed"], "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(ValueError, match="ROUTING_REVIEW_BRANCH_MISSING"):
        prepare(checked)
    assert git(checked["proposed"], "for-each-ref", "--format=%(refname) %(objectname)") == before


def agent_inputs(checked, monkeypatch):
    import cutback.agent_routing_program as program
    from cutback.agent import workspace

    root, directory = workspace(checked["base"])
    saved = {"program_id": str(uuid4()), "program_hash": "c" * 64}
    monkeypatch.setattr(program, "current", lambda *args: saved)
    refreshed = []

    def refresh(*args, **kwargs):
        refreshed.append(kwargs["purpose"])
        return checked["artifact"]

    monkeypatch.setattr(program, "proposal", refresh)
    reviewed = program.review(
        root,
        object(),
        purpose="owned-retain-routing-checks",
        contract=checked["contract"],
        receipt=checked["receipt"],
        proposed_root=checked["proposed"],
        coverage=checked["coverage"],
        branch=checked["options"]["branch"],
    )
    assert reviewed == checked["review"]
    assert (
        read_private(directory / "routing-program-review-inputs.json")["receipt"]
        == checked["receipt"]
    )
    return root, directory, refreshed


def test_routing_agent_preview_dispatch_and_revoke_refresh_current_rights(checked, monkeypatch):
    from cutback.agent_routing_publication import publish_review

    root, directory, refreshed = agent_inputs(checked, monkeypatch)
    prepared = prepare(checked)
    remote = OwnedGitHub(prepared, base_entries=checked["entries"])
    api = api_for(remote)
    options = {k: v for k, v in checked["options"].items() if k != "branch"}
    try:
        preview = publish_review(root, object(), purpose="owned-preview", **options)
        assert preview["status"] == "PREVIEW_ONLY_EXPLICIT_PUBLICATION_REQUIRED"
        assert not remote.requests and not (directory / "routing-program-publication.json").exists()
        result = publish_review(
            root, object(), purpose="owned-upload", publish=True, api=api, **options
        )
        assert result["draft"] and len(remote.posts) == 5
        assert (
            publish_review(
                root, object(), purpose="owned-reconcile", publish=True, api=api, **options
            )
            == result
        )
        assert len(remote.posts) == 5 and len(refreshed) == 4
        retained = read_private(directory / "routing-program-publication.json")
        assert checked["artifact"]["proposal"]["baseline"]["instruction"] not in json.dumps(
            retained
        )

        def revoke(*args, **kwargs):
            raise ValueError("OWNED_CURRENT_ROUTING_RIGHTS_REVOKED")

        monkeypatch.setattr("cutback.agent_routing_program.proposal", revoke)
        before = list(remote.requests)
        with pytest.raises(ValueError, match="CURRENT_ROUTING_RIGHTS_REVOKED"):
            publish_review(
                root, object(), purpose="owned-revoked", publish=True, api=api, **options
            )
        assert remote.requests == before
    finally:
        api.close()


@pytest.mark.parametrize("malformed", [None, [], "OWNED_PRIVATE_REVIEW_BODY"])
def test_routing_malformed_review_stops_before_any_github_request(checked, monkeypatch, malformed):
    from cutback.agent_routing_publication import publish_review

    root, directory, _ = agent_inputs(checked, monkeypatch)
    write_private(directory / "routing-program-review.json", malformed)
    with pytest.raises(ValueError, match="CURRENT_ROUTING_REVIEW_REQUIRED"):
        publish_review(
            root,
            object(),
            purpose="owned-malformed",
            publish=True,
            api=lambda *args: pytest.fail("MALFORMED_REVIEW_CONTACTED_GITHUB"),
            **{k: v for k, v in checked["options"].items() if k != "branch"},
        )


def test_routing_publication_verifies_actual_v2_container_receipt(tmp_path, container_image):
    value = checked_routing(tmp_path, container=container_image)
    prepared = prepare(value)
    assert value["contract"].schema_version == "cutback.application-check.v2"
    assert prepared["intent"]["application_receipt_hash"] == value["receipt"]["receipt_hash"]
