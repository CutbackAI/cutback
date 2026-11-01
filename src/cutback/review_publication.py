"""Publish one checked commit as a GitHub draft without pushing local history.

Every mutation is journaled before dispatch. A lost acknowledgement is reconciled
by reading the expected content-addressed object or exact PR; it is never replayed
automatically. This module does not approve experiments, merge or deploy.
"""

import base64
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from cutback.application_checks import git
from cutback.core.prompts import fingerprint

CONTRACT = "cutback.review-publication.v1"
SHA = re.compile(r"[a-f0-9]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def _sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError("REVIEW_PUBLICATION_REQUIRES_SHA1_GIT_OBJECTS")
    return value


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _branch(root, value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,120}", value):
        raise ValueError("REVIEW_PUBLICATION_BRANCH_INVALID")
    git(root, "check-ref-format", "--branch", value)
    return value


def _identity(raw):
    match = re.fullmatch(
        rb"([^\n<>]{1,256}) <([^\n<>]{1,256})> (-?[0-9]{1,12}) ([+-][0-9]{4})", raw
    )
    if not match:
        raise ValueError("REVIEW_PUBLICATION_COMMIT_IDENTITY_UNSUPPORTED")
    name, email, timestamp, offset = match.groups()
    hours, minutes = int(offset[1:3]), int(offset[3:5])
    if hours > 23 or minutes > 59:
        raise ValueError("REVIEW_PUBLICATION_COMMIT_IDENTITY_UNSUPPORTED")
    zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (-1 if offset[:1] == b"-" else 1)))
    try:
        date = datetime.fromtimestamp(int(timestamp), zone).isoformat()
        return {"name": name.decode("utf-8"), "email": email.decode("utf-8"), "date": date}
    except (UnicodeError, ValueError, OverflowError, OSError):
        raise ValueError("REVIEW_PUBLICATION_COMMIT_IDENTITY_UNSUPPORTED") from None


def prepare_publication(review, artifact, base, proposed, *, repository, base_branch, head_branch):
    """Build exact Git objects from an already refreshed application review.

    The proposed commit must have exactly the checked base as its parent. The
    remote only receives that one blob, tree and commit; local ancestors, refs,
    credentials, check output and permitted evaluation content are never sent.
    """
    if (
        review.get("contract") != "cutback.campaign-application-review.v1"
        or review.get("status") != "READY_FOR_PR_REVIEW"
        or review.get("production_deployed") is not False
        or review.get("review_sha256")
        != fingerprint({k: v for k, v in review.items() if k != "review_sha256"})
        or review.get("diff_sha256") != artifact.get("diff_sha256")
    ):
        raise ValueError("REVIEW_PUBLICATION_CURRENT_APPLICATION_REVIEW_REQUIRED")
    return _prepare_single_source(
        review,
        artifact,
        base,
        proposed,
        repository=repository,
        base_branch=base_branch,
        head_branch=head_branch,
    )


def _prepare_single_source(
    review, artifact, base, proposed, *, repository, base_branch, head_branch
):
    """Project a domain-verified review into one checked Git change; upload no ancestors."""
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise ValueError("REVIEW_PUBLICATION_REPOSITORY_INVALID")
    base_branch, head_branch = _branch(base, base_branch), _branch(proposed, head_branch)
    if base_branch == head_branch:
        raise ValueError("REVIEW_PUBLICATION_REQUIRES_SEPARATE_HEAD_BRANCH")
    before, after = _sha(review["base_commit"]), _sha(review["proposed_commit"])
    raw = git(proposed, "cat-file", "commit", after)
    if len(raw) > 65536 or b"\x00" in raw:
        raise ValueError("REVIEW_PUBLICATION_COMMIT_CONTENT_BOUND")
    headers, separator, message = raw.partition(b"\n\n")
    entries = headers.split(b"\n")
    if (
        not separator
        or len(entries) != 4
        or [e.split(b" ", 1)[0] for e in entries] != [b"tree", b"parent", b"author", b"committer"]
    ):
        raise ValueError("REVIEW_PUBLICATION_REQUIRES_ONE_UNSIGNED_CHECKED_PARENT")
    tree = _sha(entries[0][5:].decode("ascii"))
    if entries[1][7:] != before.encode():
        raise ValueError("REVIEW_PUBLICATION_PROPOSED_PARENT_MUST_EQUAL_CHECKED_BASE")
    source = artifact["source_file"]
    # An exact path lookup cannot become a pathspec or a Git revision option.
    fields = []
    for root, commit in ((base, before), (proposed, after)):
        row = git(root, "--literal-pathspecs", "ls-tree", "-z", commit, "--", source)
        prefix, tab, name = row.rstrip(b"\0").partition(b"\t")
        parts = prefix.split(b" ")
        if not tab or name != source.encode() or len(parts) != 3 or parts[1] != b"blob":
            raise ValueError("REVIEW_PUBLICATION_TRACKED_SOURCE_BLOB_REQUIRED")
        fields.append(parts)
    if fields[0][0] != fields[1][0] or fields[1][0] not in {b"100644", b"100755"}:
        raise ValueError("REVIEW_PUBLICATION_SOURCE_MODE_CHANGED")
    expected_diff = (
        b":"
        + fields[0][0]
        + b" "
        + fields[1][0]
        + b" "
        + fields[0][2]
        + b" "
        + fields[1][2]
        + b" M\0"
        + source.encode()
        + b"\0"
    )
    if (
        git(
            proposed,
            "diff-tree",
            "--no-commit-id",
            "--no-renames",
            "--raw",
            "-r",
            "-z",
            before,
            after,
        )
        != expected_diff
    ):
        raise ValueError("REVIEW_PUBLICATION_CONTAINS_UNREVIEWED_GIT_TREE_CHANGES")
    blob = _sha(fields[1][2].decode("ascii"))
    content = git(proposed, "cat-file", "blob", blob)
    if len(content) > 1048576:
        raise ValueError("REVIEW_PUBLICATION_SOURCE_EXCEEDS_ONE_MIB")
    try:
        content.decode("utf-8")
        message_text = message.decode("utf-8")
    except UnicodeError:
        raise ValueError("REVIEW_PUBLICATION_UTF8_SOURCE_AND_COMMIT_REQUIRED") from None
    if b"\0" in content:
        raise ValueError("REVIEW_PUBLICATION_TEXT_SOURCE_REQUIRED")
    previous = git(base, "cat-file", "blob", fields[0][2].decode("ascii"))
    if (
        hashlib.sha256(content).hexdigest() != artifact["verification"]["after_sha256"]
        or hashlib.sha256(previous).hexdigest() != artifact["source_sha256"]
    ):
        raise ValueError("REVIEW_PUBLICATION_CHECKED_BYTES_DIFFER_FROM_GIT_BLOBS")
    base_tree = _sha(git(base, "rev-parse", before + "^{tree}").decode().strip())
    commit = {
        "message": message_text,
        "tree": tree,
        "parents": [before],
        "author": _identity(entries[2][7:]),
        "committer": _identity(entries[3][10:]),
    }
    intent = {
        "contract": CONTRACT,
        "repository": repository,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "base_commit": before,
        "proposed_commit": after,
        "base_git_tree": base_tree,
        "proposed_git_tree": tree,
        "source_blob": blob,
        "source_files": [source],
        "publishes_git_author_and_message": True,
        "publishes_local_ancestors": False,
        "review_sha256": review["review_sha256"],
        "application_contract_hash": review["application_contract_hash"],
        "application_receipt_hash": review["application_receipt_hash"],
        "evidence_scope": review["evidence_scope"],
    }
    intent["intent_sha256"] = fingerprint(intent)
    marker = "<!-- cutback-review:v1:" + intent["review_sha256"] + " -->"
    body = (
        "Prepare the checked application change for human review.\n\n"
        f"Evidence scope: `{intent['evidence_scope']}`. Local checks are not independently "
        "attested and do not establish customer quality or production savings.\n\n"
        f"Checked base: `{before}`\n\nChecked proposal: `{after}`\n\n"
        f"Application contract: `{intent['application_contract_hash']}`\n\n"
        f"Application receipt: `{intent['application_receipt_hash']}`\n\n"
        "Re-run the application's CI against the current head before review. "
        "This draft does not authorize merge, deployment or traffic changes.\n\n" + marker
    )
    return {
        "intent": intent,
        "blob": {"encoding": "base64", "content": base64.b64encode(content).decode("ascii")},
        "tree": {
            "base_tree": base_tree,
            "tree": [{"path": source, "mode": fields[1][0].decode(), "type": "blob", "sha": blob}],
        },
        "commit": commit,
        "pull_request": {
            "title": "Review verified Cutback improvement",
            "head": head_branch,
            "base": base_branch,
            "body": body,
            "draft": True,
            "maintainer_can_modify": False,
        },
        "marker": marker,
    }


class GitHubReviewAPI:
    """Fixed-host, bounded, no-redirect GitHub transport; token stays in memory."""

    def __init__(self, token, *, transport=None):
        import httpx

        if (
            not isinstance(token, str)
            or not 8 <= len(token) <= 4096
            or re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token) is None
        ):
            raise ValueError("CUTBACK_GITHUB_TOKEN_REQUIRED_FOR_EXPLICIT_PUBLICATION")
        self.deadline = time.monotonic() + 180
        self.client = httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "cutback-review-publication",
            },
            timeout=10,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def __call__(self, method, path, body=None):
        import httpx

        if method not in {"GET", "POST"} or not path.startswith("/repos/") or "//" in path:
            raise ValueError("REVIEW_PUBLICATION_REMOTE_PATH_REFUSED")
        if time.monotonic() >= self.deadline:
            raise ValueError("REVIEW_PUBLICATION_REMOTE_WAIT_BOUND")
        try:
            with self.client.stream(method, path, json=body) as response:
                if method == "GET" and response.status_code == 404:
                    return None
                if not 200 <= response.status_code < 300:
                    raise ValueError("REVIEW_PUBLICATION_REMOTE_REQUEST_REFUSED_OR_UNCERTAIN")
                raw = bytearray()
                for part in response.iter_bytes():
                    raw.extend(part)
                    if len(raw) > 4194304 or time.monotonic() >= self.deadline:
                        raise ValueError("REVIEW_PUBLICATION_REMOTE_RESPONSE_BOUND")
                return json.loads(raw)
        except (httpx.HTTPError, UnicodeError, json.JSONDecodeError, RecursionError):
            raise ValueError(
                "REVIEW_PUBLICATION_REMOTE_OUTCOME_UNCERTAIN_RECONCILE_SAME_INTENT"
            ) from None


def publish_prepared(prepared, api, *, journal, save):
    """Create only missing expected objects; never update an existing ref or PR."""
    intent = prepared["intent"]
    if journal.get("intent") != intent or not isinstance(journal.get("dispatched"), list):
        raise ValueError("REVIEW_PUBLICATION_JOURNAL_INTENT_CHANGED")
    prefix = "/repos/" + intent["repository"]

    def post_once(stage, path, body):
        if stage in journal["dispatched"]:
            raise ValueError("REVIEW_PUBLICATION_UNCERTAIN_MUTATION_REQUIRES_RECONCILIATION")
        journal["dispatched"].append(stage)
        save(journal)
        return api("POST", prefix + path, body)

    def ref(branch, expected, *, missing=False):
        value = api("GET", prefix + "/git/ref/heads/" + quote(branch, safe=""))
        if value is None and missing:
            return None
        if (
            not isinstance(value, dict)
            or value.get("ref") != "refs/heads/" + branch
            or _mapping(value.get("object")).get("type") != "commit"
            or _mapping(value.get("object")).get("sha") != expected
        ):
            raise ValueError("REVIEW_PUBLICATION_REMOTE_BRANCH_CHANGED_OR_UNAVAILABLE")
        return value

    ref(intent["base_branch"], intent["base_commit"])
    remote_base = api("GET", prefix + "/git/commits/" + intent["base_commit"])
    if (
        not isinstance(remote_base, dict)
        or remote_base.get("sha") != intent["base_commit"]
        or _mapping(remote_base.get("tree")).get("sha") != intent["base_git_tree"]
    ):
        raise ValueError("REVIEW_PUBLICATION_REMOTE_BASE_TREE_CHANGED")
    ref(intent["head_branch"], intent["proposed_commit"], missing=True)
    for stage, resource, expected in (
        ("blob", "blobs", intent["source_blob"]),
        ("tree", "trees", intent["proposed_git_tree"]),
        ("commit", "commits", intent["proposed_commit"]),
    ):
        path = "/git/" + resource
        value = api("GET", prefix + path + "/" + expected)
        if value is None:
            value = post_once(stage, path, prepared[stage])
        if not isinstance(value, dict) or value.get("sha") != expected:
            raise ValueError("REVIEW_PUBLICATION_REMOTE_GIT_OBJECT_CHANGED")
    if ref(intent["head_branch"], intent["proposed_commit"], missing=True) is None:
        ref(intent["base_branch"], intent["base_commit"])
        value = post_once(
            "branch",
            "/git/refs",
            {"ref": "refs/heads/" + intent["head_branch"], "sha": intent["proposed_commit"]},
        )
        if (
            not isinstance(value, dict)
            or _mapping(value.get("object")).get("sha") != intent["proposed_commit"]
        ):
            raise ValueError("REVIEW_PUBLICATION_REMOTE_BRANCH_CHANGED_OR_UNAVAILABLE")
    ref(intent["head_branch"], intent["proposed_commit"])
    ref(intent["base_branch"], intent["base_commit"])
    owner = intent["repository"].split("/")[0]
    query = urlencode(
        {
            "state": "all",
            "head": owner + ":" + intent["head_branch"],
            "base": intent["base_branch"],
            "per_page": 100,
        }
    )
    pulls = api("GET", prefix + "/pulls?" + query)
    if not isinstance(pulls, list) or len(pulls) >= 100:
        raise ValueError("REVIEW_PUBLICATION_PULL_REQUEST_LOOKUP_INCOMPLETE")
    if len(pulls) > 1:
        raise ValueError("REVIEW_PUBLICATION_PULL_REQUEST_IDENTITY_AMBIGUOUS")
    pull = pulls[0] if pulls else post_once("pull_request", "/pulls", prepared["pull_request"])
    head, base = (_mapping(_mapping(pull).get(key)) for key in ("head", "base"))
    if (
        not isinstance(pull, dict)
        or type(pull.get("number")) is not int
        or pull["number"] < 1
        or pull.get("state") != "open"
        or pull.get("merged_at") is not None
        or head.get("sha") != intent["proposed_commit"]
        or head.get("ref") != intent["head_branch"]
        or str(_mapping(head.get("repo")).get("full_name", "")).casefold()
        != intent["repository"].casefold()
        or base.get("sha") != intent["base_commit"]
        or base.get("ref") != intent["base_branch"]
        or str(_mapping(base.get("repo")).get("full_name", "")).casefold()
        != intent["repository"].casefold()
        or not isinstance(pull.get("body"), str)
        or prepared["marker"] not in pull["body"]
        or str(pull.get("html_url", "")).casefold()
        != f"https://github.com/{intent['repository']}/pull/{pull['number']}".casefold()
        or type(pull.get("draft")) is not bool
        or not pulls
        and pull.get("draft") is not True
    ):
        raise ValueError("REVIEW_PUBLICATION_PULL_REQUEST_MOVED_CLOSED_OR_NOT_BOUND")
    # Ref movement during publication never becomes an accepted exact-head receipt.
    ref(intent["head_branch"], intent["proposed_commit"])
    ref(intent["base_branch"], intent["base_commit"])
    result = {
        "contract": CONTRACT,
        "intent_sha256": intent["intent_sha256"],
        "review_sha256": intent["review_sha256"],
        "repository": intent["repository"],
        "base_commit": intent["base_commit"],
        "proposed_commit": intent["proposed_commit"],
        "pull_request_url": pull["html_url"],
        "pull_request_number": pull["number"],
        "draft": pull["draft"],
        "remote_published": True,
        "provider_calls": 0,
        "application_commands_executed": 0,
        "merge_authorized": False,
        "production_deployed": False,
    }
    journal["result"] = result
    save(journal)
    return result
