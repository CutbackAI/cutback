import copy
import hashlib
import json
import shutil

import httpx
import pytest
from support_review_publication import OwnedGitHub, api_for, journal_for
from test_application_checks import repository

from cutback.application_checks import ApplicationCommand, git, prepare, run_checks
from cutback.campaign_review import review_change
from cutback.review_publication import GitHubReviewAPI, prepare_publication, publish_prepared


@pytest.fixture
def checked_publication(tmp_path):
    base = repository(tmp_path / "base", "print('A')\n")
    # Publication binds checked bytes to Git blobs, without checkout EOL conversion.
    (base / "sample.py").write_bytes(b"print('A')\n")
    proposed = tmp_path / "proposed"
    shutil.copytree(base, proposed)
    (proposed / "sample.py").write_bytes(b"print('B')\n")
    git(proposed, "add", "sample.py")
    git(
        proposed,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Owned checked improvement",
    )
    hook = proposed / ".git/hooks/pre-push"
    hook.write_text("#!/bin/sh\nprintf executed > OWNED_HOOK_MUST_NOT_RUN\n", encoding="utf-8")
    hook.chmod(0o755)
    contract = prepare(base, proposed, [ApplicationCommand(key="smoke", arguments=["sample.py"])])
    receipt = run_checks(contract, base, proposed, execute_local=True)
    artifact = {
        "status": "DRY_RUN_ONLY",
        "production_deployed": False,
        "campaign_id": "synthetic-campaign",
        "campaign_hash": "a" * 64,
        "run_id": "synthetic-run",
        "plan_hash": "b" * 64,
        "confirmation_gate": {"passed": True, "scope": "sample_only", "plan_hash": "b" * 64},
        "diff": "owned synthetic diff",
        "rollback_diff": "owned rollback",
        "diff_sha256": hashlib.sha256(b"owned synthetic diff").hexdigest(),
        "source_file": "sample.py",
        "source_commit": contract.base.commit,
        "source_sha256": contract.base.files["sample.py"],
        "verification": {"after_sha256": contract.proposed.files["sample.py"]},
        "affected_workflows": ["classify"],
        "unresolved_consumer_workflows": [],
        "scanner_warning_codes": [],
    }
    coverage = {"classify": ["smoke"]}
    review = review_change(
        artifact, contract, receipt, base, proposed, coverage, branch="cutback/checked-review"
    )
    prepared = prepare_publication(
        review,
        artifact,
        base,
        proposed,
        repository="example-owner/task-app",
        base_branch="main",
        head_branch="cutback/checked-review",
    )
    return {
        "base": base,
        "proposed": proposed,
        "artifact": artifact,
        "review": review,
        "prepared": prepared,
        "contract": contract,
        "receipt": receipt,
        "coverage": coverage,
    }


def test_exact_objects_draft_and_idempotent_reconciliation(checked_publication):
    prepared = checked_publication["prepared"]
    remote, journal, saves = OwnedGitHub(prepared), journal_for(prepared), []
    api = api_for(remote)
    try:
        result = publish_prepared(
            prepared, api, journal=journal, save=lambda v: saves.append(copy.deepcopy(v))
        )
        assert result["proposed_commit"] == prepared["intent"]["proposed_commit"]
        assert result["draft"] and result["remote_published"]
        assert result["merge_authorized"] is False and result["production_deployed"] is False
        assert remote.posts == ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
        assert [value["dispatched"][-1] for value in saves[:-1]] == [
            "blob",
            "tree",
            "commit",
            "branch",
            "pull_request",
        ]
        assert publish_prepared(prepared, api, journal=journal, save=lambda _: None) == result
        assert len(remote.posts) == 5
        assert len(remote.pulls) == 1
        serialized = json.dumps(saves)
        assert "synthetic-github-test-token" not in serialized and "print('B')" not in serialized
        assert not (checked_publication["proposed"] / "OWNED_HOOK_MUST_NOT_RUN").exists()
    finally:
        api.close()


@pytest.mark.parametrize(
    "lost", ["/git/blobs", "/git/trees", "/git/commits", "/git/refs", "/pulls"]
)
def test_lost_acknowledgement_recovers_exact_object_without_duplicate_post(
    checked_publication, lost
):
    prepared = checked_publication["prepared"]
    remote = OwnedGitHub(prepared, lose=lost)
    journal = journal_for(prepared)
    api = api_for(remote)
    try:
        with pytest.raises(ValueError, match="REMOTE_OUTCOME_UNCERTAIN") as error:
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert "synthetic-secret" not in str(error.value)
        result = publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert result["remote_published"] and len(remote.pulls) == 1
        assert remote.posts.count(lost) == 1
    finally:
        api.close()


def test_uncertain_unobserved_mutation_is_not_replayed(checked_publication):
    prepared = checked_publication["prepared"]
    remote = OwnedGitHub(prepared)
    journal = journal_for(prepared)
    journal["dispatched"].append("blob")
    api = api_for(remote)
    try:
        with pytest.raises(ValueError, match="UNCERTAIN_MUTATION_REQUIRES_RECONCILIATION"):
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert remote.posts == []
    finally:
        api.close()


@pytest.mark.parametrize("branch", ["main", "cutback/checked-review"])
def test_remote_branch_movement_stops_before_upload(checked_publication, branch):
    prepared = checked_publication["prepared"]
    remote = OwnedGitHub(prepared)
    remote.refs[branch] = "c" * 40
    api = api_for(remote)
    try:
        with pytest.raises(ValueError, match="REMOTE_BRANCH_CHANGED"):
            publish_prepared(prepared, api, journal=journal_for(prepared), save=lambda _: None)
        assert remote.posts == []
    finally:
        api.close()


def test_pr_creation_race_cannot_produce_exact_head_acceptance(checked_publication):
    prepared = checked_publication["prepared"]
    remote = OwnedGitHub(prepared)
    remote.move_after_pr = True
    journal = journal_for(prepared)
    api = api_for(remote)
    try:
        with pytest.raises(ValueError, match="REMOTE_BRANCH_CHANGED"):
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert len(remote.pulls) == 1 and "result" not in journal
    finally:
        api.close()


@pytest.mark.parametrize("change", ["closed", "foreign", "moved", "marker", "malformed"])
def test_existing_pull_request_cannot_be_reused_with_different_identity(
    checked_publication, change
):
    prepared = checked_publication["prepared"]
    remote, journal = OwnedGitHub(prepared), journal_for(prepared)
    api = api_for(remote)
    try:
        publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        if change == "closed":
            remote.pulls[0]["state"] = "closed"
        elif change == "foreign":
            remote.pulls[0]["head"]["repo"]["full_name"] = "other-owner/task-app"
        elif change == "moved":
            remote.pulls[0]["base"]["sha"] = "c" * 40
        elif change == "marker":
            remote.pulls[0]["body"] = "Different change"
        else:
            remote.pulls[0]["head"] = None
        with pytest.raises(ValueError, match="PULL_REQUEST_MOVED_CLOSED_OR_NOT_BOUND"):
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert len(remote.posts) == 5 and len(remote.pulls) == 1
    finally:
        api.close()


def test_checked_parent_and_branch_names_required(checked_publication):
    value = checked_publication
    options = dict(
        repository="example-owner/task-app", base_branch="main", head_branch="cutback/review"
    )
    for field, replacement in (
        ("repository", "https://other.invalid/repo"),
        ("head_branch", "--unsafe"),
        ("head_branch", "main"),
    ):
        with pytest.raises(ValueError, match="REVIEW_PUBLICATION"):
            prepare_publication(
                value["review"],
                value["artifact"],
                value["base"],
                value["proposed"],
                **{**options, field: replacement},
            )
    git(
        value["proposed"],
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "--allow-empty",
        "-m",
        "Another local ancestor must not be uploaded",
    )
    review = {
        **value["review"],
        "proposed_commit": git(value["proposed"], "rev-parse", "HEAD").decode().strip(),
    }
    from cutback.core.prompts import fingerprint

    review["review_sha256"] = fingerprint({k: v for k, v in review.items() if k != "review_sha256"})
    with pytest.raises(ValueError, match="PROPOSED_PARENT_MUST_EQUAL_CHECKED_BASE"):
        prepare_publication(review, value["artifact"], value["base"], value["proposed"], **options)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://other.invalid"}),
        httpx.Response(403, text="synthetic-secret-MUST-NOT-LEAK"),
        httpx.Response(200, content=b"invalid-json"),
    ],
)
def test_remote_transport_never_redirects_or_discloses_error_body(response):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return response

    api = GitHubReviewAPI("synthetic-github-test-token", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="REVIEW_PUBLICATION") as error:
            api("GET", "/repos/example-owner/task-app/pulls")
        assert "synthetic-secret" not in str(error.value)
        assert seen == ["api.github.com"]
    finally:
        api.close()


@pytest.mark.parametrize("extra_mode", [False, True])
def test_git_mode_changes_cannot_hide_in_file_content_receipts(checked_publication, extra_mode):
    value = checked_publication
    base, proposed = value["base"], value["proposed"]
    if extra_mode:
        (base / "notes.txt").write_bytes(b"Owned stable notes\n")
        git(base, "add", "notes.txt")
        git(
            base,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Owned second file",
        )
        copied = proposed.parent / "proposed-extra-mode"
        shutil.copytree(base, copied)
        proposed = copied
        (proposed / "sample.py").write_bytes(b"print('B')\n")
        git(proposed, "add", "sample.py")
    name = "notes.txt" if extra_mode else "sample.py"
    (proposed / name).chmod(0o755)
    git(proposed, "update-index", "--chmod=+x", "--", name)
    git(
        proposed,
        "-c",
        "user.name=Synthetic",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        *([] if extra_mode else ["--amend"]),
        "-m",
        "Owned executable change",
    )
    contract = prepare(base, proposed, value["contract"].commands)
    receipt = run_checks(contract, base, proposed, execute_local=True)
    artifact = {**value["artifact"], "source_commit": contract.base.commit}
    reviewed = review_change(artifact, contract, receipt, base, proposed, value["coverage"])
    code = "UNREVIEWED_GIT_TREE_CHANGES" if extra_mode else "SOURCE_MODE_CHANGED"
    with pytest.raises(ValueError, match=code):
        prepare_publication(
            reviewed,
            artifact,
            base,
            proposed,
            repository="example-owner/task-app",
            base_branch="main",
            head_branch="cutback/review",
        )


def test_checkout_eol_conversion_requires_matching_blob_checks(checked_publication):
    value = checked_publication
    (value["base"] / "sample.py").write_bytes(b"print('A')\r\n")
    contract = prepare(value["base"], value["proposed"], value["contract"].commands)
    receipt = run_checks(contract, value["base"], value["proposed"], execute_local=True)
    artifact = {**value["artifact"], "source_sha256": contract.base.files["sample.py"]}
    reviewed = review_change(
        artifact, contract, receipt, value["base"], value["proposed"], value["coverage"]
    )
    with pytest.raises(ValueError, match="CHECKED_BYTES_DIFFER_FROM_GIT_BLOBS"):
        prepare_publication(
            reviewed,
            artifact,
            value["base"],
            value["proposed"],
            repository="example-owner/task-app",
            base_branch="main",
            head_branch="cutback/review",
        )


def test_intent_changes_and_failed_journal_write_cannot_dispatch(checked_publication):
    prepared = checked_publication["prepared"]
    remote = OwnedGitHub(prepared)
    api = api_for(remote)
    try:
        journal = journal_for(prepared)
        journal["intent"]["repository"] = "other-owner/task-app"
        with pytest.raises(ValueError, match="JOURNAL_INTENT_CHANGED"):
            publish_prepared(prepared, api, journal=journal, save=lambda _: None)
        assert remote.requests == []

        def unavailable(_):
            raise OSError("Owned synthetic journal storage unavailable")

        with pytest.raises(OSError, match="journal storage unavailable"):
            publish_prepared(prepared, api, journal=journal_for(prepared), save=unavailable)
        assert remote.posts == []
    finally:
        api.close()


def test_remote_json_response_has_a_finite_size_limit():
    api = GitHubReviewAPI(
        "synthetic-github-test-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * 4194305)),
    )
    try:
        with pytest.raises(ValueError, match="REMOTE_RESPONSE_BOUND"):
            api("GET", "/repos/example-owner/task-app/pulls")
    finally:
        api.close()


def test_local_replace_refs_cannot_substitute_the_checked_git_objects(checked_publication):
    from cutback.application_checks import snapshot

    value = checked_publication
    before, after = (value["review"][key] for key in ("base_commit", "proposed_commit"))
    git(value["proposed"], "replace", after, before)
    assert (
        git(value["proposed"], "for-each-ref", "--format=%(objectname)", "refs/replace/" + after)
        .decode()
        .strip()
        == before
    )
    assert snapshot(value["proposed"]) == value["contract"].proposed
    prepared = prepare_publication(
        value["review"],
        value["artifact"],
        value["base"],
        value["proposed"],
        repository="example-owner/task-app",
        base_branch="main",
        head_branch="cutback/checked-review",
    )
    assert prepared == value["prepared"]
