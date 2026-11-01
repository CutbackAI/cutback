"""A resumable synthetic discussion in an explicitly selected owned local incident."""

import argparse
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cutback.core.config import validate_endpoint
from cutback.core.workspace import (
    prepare_private_output,
    read_private,
    workspace_lock,
    write_private,
)

TEXT = (
    "Synthetic investigation note: the sampled execution failed; its root cause remains unverified."
)
REVISION = "Synthetic follow-up: a regression case is proposed; task quality remains unverified."


def demonstrate(
    endpoint, project, incident, policy, token, receipt, *, execute_local=False, transport=None
):
    endpoint = validate_endpoint(endpoint)
    parts = urlsplit(endpoint)
    try:
        local = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        local = False
    if not local or parts.path not in {"", "/"} or parts.query or parts.fragment:
        raise ValueError("COMMENT_EXAMPLE_REQUIRES_OWNED_NUMERIC_LOOPBACK")
    intent = {
        "endpoint": endpoint,
        "project": str(UUID(project)),
        "incident": str(UUID(incident)),
        "policy": str(UUID(policy)),
        "contract": "cutback.synthetic-comment-example.v1",
    }
    if type(execute_local) is not bool:
        raise ValueError("COMMENT_EXAMPLE_REQUIRES_EXPLICIT_BOOLEAN")
    if not execute_local:
        return {
            "status": "PREVIEW_ONLY",
            "mutations": "one synthetic comment, one revision, then erasure",
            "provider_calls": 0,
            "network_requests": 0,
        }
    import httpx

    destination = prepare_private_output(receipt)
    with workspace_lock(destination.parent):
        state = (
            read_private(destination)
            if destination.exists()
            else {
                "intent": intent,
                "comment_id": str(uuid4()),
                "first_version": str(uuid4()),
                "next_version": str(uuid4()),
                "phase": "prepared",
            }
        )
        if (
            not isinstance(state, dict)
            or state.get("intent") != intent
            or state.get("phase") not in {"prepared", "created", "revised", "deleted"}
        ):
            raise ValueError("COMMENT_EXAMPLE_JOURNAL_INTENT_CHANGED")
        for field in ("comment_id", "first_version", "next_version"):
            UUID(state[field])
        write_private(destination, state)
        base = f"/v1/projects/{project}/incidents/{incident}/comments/{state['comment_id']}"
        with httpx.Client(
            base_url=endpoint,
            headers={"Authorization": "Bearer " + token},
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
        ) as client:

            def request(method, path, body=None, expected=(200,), raw=False):
                try:
                    response = client.request(
                        method, path, **({"json": body} if body is not None else {})
                    )
                except httpx.HTTPError:
                    raise ValueError(
                        "COMMENT_EXAMPLE_RESPONSE_UNCERTAIN_RESUME_SAME_JOURNAL"
                    ) from None
                if response.status_code not in expected:
                    raise ValueError("COMMENT_EXAMPLE_CURRENT_SCOPE_OR_STATE_REFUSED")
                return response if raw else response.json()

            if state["phase"] == "prepared":
                request(
                    "PUT",
                    base,
                    {
                        "version_id": state["first_version"],
                        "expected_revision": 0,
                        "policy_id": policy,
                        "text": TEXT,
                    },
                    expected=(201,),
                )
                state["phase"] = "created"
                write_private(destination, state)
            if state["phase"] == "created":
                first = request(
                    "POST", base + "/content?revision=1", {"purpose": "synthetic-comment-review"}
                )
                if first["text"] != TEXT:
                    raise ValueError("COMMENT_EXAMPLE_FIRST_REVISION_CHANGED")
                request(
                    "PUT",
                    base,
                    {
                        "version_id": state["next_version"],
                        "expected_revision": 1,
                        "policy_id": policy,
                        "text": REVISION,
                    },
                    expected=(201,),
                )
                state["phase"] = "revised"
                write_private(destination, state)
            if state["phase"] == "revised":
                current = request(
                    "POST",
                    base + "/content",
                    {"purpose": "synthetic-revision-review"},
                    expected=(200, 410),
                    raw=True,
                )
                if current.status_code == 200:
                    history = request("GET", base + "/versions")
                    if current.json()["text"] != REVISION or [
                        row["revision"] for row in history["items"]
                    ] != [2, 1]:
                        raise ValueError("COMMENT_EXAMPLE_REVISION_HISTORY_CHANGED")
                    if TEXT in str(history) or REVISION in str(history):
                        raise ValueError("COMMENT_EXAMPLE_METADATA_CONTAINED_TEXT")
                # A 410 may be a committed erasure whose response was lost.
                # The same revision/actor-bound delete must still reconcile it;
                # an arbitrary missing or changed record never establishes success.
                request("POST", base + "/delete", {"expected_revision": 2})
                state["phase"] = "deleted"
                write_private(destination, state)
            # The same explicit command can reconcile lost create/edit/delete
            # replies using frozen IDs. Completion never recreates the comment.
            request(
                "POST",
                base + "/content",
                {"purpose": "synthetic-erasure-check"},
                expected=(410,),
                raw=True,
            )
    return {
        "status": "COMPLETED_SYNTHETIC_COMMENT_LIFECYCLE",
        "retained_text_revisions": 0,
        "comment_revisions_before_erasure": 2,
        "provider_calls": 0,
        "synthetic_only": True,
        "notifications_configured": False,
        "customer_outcome_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute-local", action="store_true")
    args = parser.parse_args()
    try:
        result = demonstrate(
            os.environ["CUTBACK_ENDPOINT"],
            os.environ["CUTBACK_PROJECT_ID"],
            os.environ["CUTBACK_INCIDENT_ID"],
            os.environ["CUTBACK_COMMENT_POLICY_ID"],
            os.environ.get("CUTBACK_CONTROL_KEY", ""),
            args.receipt,
            execute_local=args.execute_local,
        )
    except Exception:
        print("COMMENT_EXAMPLE_STOPPED_INSPECT_SAME_PRIVATE_JOURNAL")
        return 1
    import json

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
