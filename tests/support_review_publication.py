"""Owned GitHub HTTP transport with actual Git-object hashing; never external I/O."""

import base64
import hashlib
import json
from datetime import datetime
from urllib.parse import unquote

import httpx

from cutback.review_publication import GitHubReviewAPI


def object_hash(kind, value):
    return hashlib.sha1(kind.encode() + b" " + str(len(value)).encode() + b"\0" + value).hexdigest()


class OwnedGitHub:
    """Closed HTTP transport computes actual Git object IDs; no external requests."""

    def __init__(self, prepared, *, lose=None):
        self.prepared = prepared
        self.intent = prepared["intent"]
        self.refs = {"main": self.intent["base_commit"]}
        self.objects = {
            "commits": {
                self.intent["base_commit"]: {
                    "sha": self.intent["base_commit"],
                    "tree": {"sha": self.intent["base_git_tree"]},
                }
            },
            "trees": {},
            "blobs": {},
        }
        self.pulls = []
        self.posts = []
        self.requests = []
        self.lose = lose
        self.move_after_pr = False

    def ref(self, branch):
        return {
            "ref": "refs/heads/" + branch,
            "object": {"type": "commit", "sha": self.refs[branch]},
        }

    def __call__(self, request):
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer synthetic-github-test-token"
        assert request.headers["x-github-api-version"] == "2026-03-10"
        prefix = "/repos/" + self.intent["repository"]
        path = unquote(request.url.path.removeprefix(prefix))
        self.requests.append((request.method, path))
        if request.method == "GET":
            if path.startswith("/git/ref/heads/"):
                branch = path.removeprefix("/git/ref/heads/")
                return (
                    httpx.Response(200, json=self.ref(branch))
                    if branch in self.refs
                    else httpx.Response(404)
                )
            if path.startswith("/git/"):
                resource, key = path.removeprefix("/git/").split("/", 1)
                value = self.objects.get(resource, {}).get(key)
                return httpx.Response(200, json=value) if value else httpx.Response(404)
            if path == "/pulls":
                assert request.url.params["state"] == "all"
                assert request.url.params["head"] == (
                    self.intent["repository"].split("/")[0] + ":" + self.intent["head_branch"]
                )
                return httpx.Response(200, json=self.pulls)
            raise AssertionError("Unexpected GET")
        assert request.method == "POST"
        body = json.loads(request.content)
        self.posts.append(path)
        if path == "/git/blobs":
            assert body["encoding"] == "base64"
            value = {"sha": object_hash("blob", base64.b64decode(body["content"]))}
            self.objects["blobs"][value["sha"]] = value
        elif path == "/git/trees":
            assert body["base_tree"] == self.intent["base_git_tree"]
            assert len(body["tree"]) == 1
            entry = body["tree"][0]
            assert entry["path"] == self.prepared["tree"]["tree"][0]["path"]
            assert entry["type"] == "blob"
            tree = (
                entry["mode"].encode()
                + b" "
                + entry["path"].encode()
                + b"\0"
                + bytes.fromhex(entry["sha"])
            )
            value = {"sha": object_hash("tree", tree)}
            self.objects["trees"][value["sha"]] = value
        elif path == "/git/commits":

            def identity(label):
                who = body[label]
                date = datetime.fromisoformat(who["date"])
                return (
                    f"{label} {who['name']} <{who['email']}> "
                    f"{int(date.timestamp())} {date.strftime('%z')}\n"
                )

            assert body["parents"] == [self.intent["base_commit"]]
            raw = (
                f"tree {body['tree']}\nparent {body['parents'][0]}\n"
                + identity("author")
                + identity("committer")
                + "\n"
                + body["message"]
            ).encode()
            value = {"sha": object_hash("commit", raw), "tree": {"sha": body["tree"]}}
            self.objects["commits"][value["sha"]] = value
        elif path == "/git/refs":
            branch = body["ref"].removeprefix("refs/heads/")
            assert branch not in self.refs  # No update/force or overwrite path exists.
            self.refs[branch] = body["sha"]
            value = self.ref(branch)
        elif path == "/pulls":
            assert body["draft"] is True and body["maintainer_can_modify"] is False
            value = {
                "number": 1,
                "state": "open",
                "draft": True,
                "merged_at": None,
                "html_url": "https://github.com/example-owner/task-app/pull/1",
                "body": body["body"],
                "head": {
                    "sha": self.refs[body["head"]],
                    "ref": body["head"],
                    "repo": {"full_name": self.intent["repository"]},
                },
                "base": {
                    "sha": self.refs[body["base"]],
                    "ref": body["base"],
                    "repo": {"full_name": self.intent["repository"]},
                },
            }
            self.pulls.append(value)
            if self.move_after_pr:
                self.refs[self.intent["head_branch"]] = "c" * 40
        else:
            raise AssertionError("Unexpected mutation")
        if self.lose == path:
            self.lose = None
            raise httpx.ReadTimeout("untrusted response includes synthetic-secret-MUST-NOT-LEAK")
        return httpx.Response(201, json=value)


def api_for(remote):
    return GitHubReviewAPI("synthetic-github-test-token", transport=httpx.MockTransport(remote))


def journal_for(prepared):
    return {"intent": json.loads(json.dumps(prepared["intent"])), "dispatched": []}
