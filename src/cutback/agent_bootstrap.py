"""Recoverable, nonbillable local project and credential setup. This file never queues work."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError

from cutback.core.bootstrap import (
    BOOTSTRAP_CONTRACT,
    ScopedKeyInput,
    ScopedKeyReceipt,
    scoped_key_fingerprint,
)
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import read_private, workspace_lock, write_private


def binding(state):
    return fingerprint(
        {
            "version": state["version"],
            "endpoint": state["endpoint"],
            "owner": state["owner"],
            "root_fingerprint": state["root_fingerprint"],
            "project": state["project"],
            "expires_at": state["expires_at"],
            "keys": {
                role: scoped_key_fingerprint(
                    key_spec(state, role), project_id=state["project"]["id"]
                )
                for role in ("project_admin", "ingest")
            },
        }
    )


def key_body(state, role):
    return {
        **state["keys"][role],
        "role": role,
        "environment": "staging",
        "expires_at": state["expires_at"],
    }


def key_spec(state, role):
    return ScopedKeyInput.model_validate(key_body(state, role))


def validate_connection(root, value, *, owner=None):
    if "bootstrap_contract" not in value:
        return
    if value["bootstrap_contract"] != BOOTSTRAP_CONTRACT:
        raise ValueError("BOOTSTRAP_CONNECTION_PROTOCOL_UNSUPPORTED")
    try:
        receipt = value["bootstrap_receipt"]
        if owner is None:
            from cutback.local_stack import configured

            _, config = configured(root)
            owner = config["owner"]
        owner_matches = receipt["owner"] == str(UUID(str(owner)))
        root_matches = receipt["root_fingerprint"] == fingerprint(str(root.resolve(strict=True)))
        expires_at = datetime.fromisoformat(receipt["expires_at"])
        if expires_at.tzinfo is None:
            raise ValueError("NAIVE_EXPIRY")
        project_id = str(UUID(value["project"]))
        restored = {
            "version": value["bootstrap_contract"],
            "endpoint": value["endpoint"],
            "owner": receipt["owner"],
            "root_fingerprint": receipt["root_fingerprint"],
            "project": {
                "id": project_id,
                "slug": "application-" + project_id[:12],
                "environment": "staging",
            },
            "expires_at": receipt["expires_at"],
            "keys": {
                role: {"request_id": receipt["key_ids"][role], "token": value[field]}
                for role, field in (("project_admin", "token"), ("ingest", "ingest_token"))
            },
        }
        intent_matches = receipt["intent_sha256"] == binding(restored)
    except (KeyError, TypeError, OSError, ValueError):
        raise ValueError("PRIVATE_BOOTSTRAP_CONNECTION_INVALID") from None
    if not owner_matches or not root_matches or not intent_matches:
        raise ValueError("BOOTSTRAP_CONNECTION_DESTINATION_CHANGED")
    if expires_at <= datetime.now(UTC):
        raise ValueError("BOOTSTRAP_AUTHORITY_EXPIRED_NEW_REVIEW_REQUIRED")


def bootstrap_connection(root, control, *, owner):
    from cutback.agent import workspace

    _, directory = workspace(root)
    destination = directory / "connection.json"
    journal = directory / "bootstrap-credentials.json"
    owner = str(UUID(str(owner)))
    with workspace_lock(directory):
        if destination.exists():
            existing = read_private(destination)
            if existing["endpoint"] != control.endpoint:
                raise ValueError("SAVED_COLLECTOR_DESTINATION_CHANGED")
            validate_connection(root, existing, owner=owner)
            return existing
        present = journal.exists()
        state = read_private(journal) if present else None
        if present:
            if not isinstance(state, dict):
                raise ValueError("PRIVATE_BOOTSTRAP_INTENT_INVALID")
            try:
                valid = (
                    state["version"] == BOOTSTRAP_CONTRACT
                    and state["owner"] == owner
                    and state["endpoint"] == control.endpoint
                    and state["root_fingerprint"] == fingerprint(str(root.resolve(strict=True)))
                    and state["intent_sha256"] == binding(state)
                    and UUID(state["project"]["id"]).version == 4
                )
            except (KeyError, ValueError, TypeError):
                raise ValueError("PRIVATE_BOOTSTRAP_INTENT_INVALID") from None
            if not valid:
                raise ValueError("PRIVATE_BOOTSTRAP_INTENT_CHANGED")
            if state.get("conflict"):
                raise ValueError("BOOTSTRAP_IDENTITY_RECONCILIATION_REQUIRED")
            if datetime.fromisoformat(state["expires_at"]) <= datetime.now(UTC):
                raise ValueError("BOOTSTRAP_AUTHORITY_EXPIRED_NEW_REVIEW_REQUIRED")
        try:
            ready = control("GET", "/v1/health/ready")
        except ValueError:
            raise ValueError("BOOTSTRAP_COLLECTOR_UNAVAILABLE_RESUME_SAVED_INTENT") from None
        if not isinstance(ready, dict) or ready.get("bootstrap_contract") != BOOTSTRAP_CONTRACT:
            raise ValueError("COLLECTOR_BOOTSTRAP_UPGRADE_REQUIRED")
        if state is None:
            identifier = str(uuid4())
            state = {
                "version": BOOTSTRAP_CONTRACT,
                "owner": owner,
                "root_fingerprint": fingerprint(str(root.resolve(strict=True))),
                "endpoint": control.endpoint,
                "project": {
                    "id": identifier,
                    "slug": "application-" + identifier[:12],
                    "environment": "staging",
                },
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "keys": {
                    role: {"request_id": str(uuid4()), "token": secrets.token_urlsafe(48)}
                    for role in ("project_admin", "ingest")
                },
            }
            state["intent_sha256"] = binding(state)
            # This is a private credential file, separate from metadata sessions.
            write_private(journal, state, exclusive=True)

        def stop(resource, expected):
            state["conflict"] = {"resource": resource, "expected_request_id": expected}
            write_private(journal, state)
            raise ValueError("BOOTSTRAP_IDENTITY_RECONCILIATION_REQUIRED")

        def request(path, body):
            try:
                return control("POST", path, body)
            except ValueError as exc:
                if str(exc) in {
                    "PROJECT_BOOTSTRAP_IDENTITY_CONFLICT",
                    "PROJECT_BOOTSTRAP_WAS_DELETED",
                    "SCOPED_KEY_PREPARATION_IDENTITY_CONFLICT",
                    "SCOPED_KEY_NO_LONGER_AVAILABLE",
                    "SCOPED_KEY_CREDENTIAL_IDENTITY_CONFLICT",
                    "SCOPED_KEY_EXPIRED_OR_REVOKED",
                }:
                    stop("bootstrap_authority", state["project"]["id"])
                raise ValueError("BOOTSTRAP_COLLECTOR_UNAVAILABLE_RESUME_SAVED_INTENT") from None

        project = state["project"]
        result = request(
            "/v1/projects?request_id=" + project["id"],
            {"slug": project["slug"], "environment": project["environment"]},
        )
        if not isinstance(result, dict) or result != project:
            stop("projects", project["id"])
        for role in ("project_admin", "ingest"):
            expected = key_spec(state, role)
            result = request(
                "/v1/projects/" + project["id"] + "/scoped-keys", key_body(state, role)
            )
            try:
                receipt = ScopedKeyReceipt.model_validate(result)
            except (ValidationError, ValueError, TypeError):
                stop("scoped_keys", str(expected.request_id))
            if (
                receipt.id != expected.request_id
                or receipt.project_id != UUID(project["id"])
                or receipt.role != role
                or receipt.environment != "staging"
                or receipt.expires_at != expected.expires_at
                or receipt.request_sha256
                != scoped_key_fingerprint(expected, project_id=project["id"])
            ):
                stop("scoped_keys", str(expected.request_id))
        value = {
            "endpoint": control.endpoint,
            "project": project["id"],
            "token": state["keys"]["project_admin"]["token"],
            "ingest_token": state["keys"]["ingest"]["token"],
            "bootstrap_contract": BOOTSTRAP_CONTRACT,
            "bootstrap_receipt": {
                "intent_sha256": state["intent_sha256"],
                "expires_at": state["expires_at"],
                "owner": owner,
                "root_fingerprint": state["root_fingerprint"],
                "key_ids": {
                    role: state["keys"][role]["request_id"] for role in ("project_admin", "ingest")
                },
            },
        }
        write_private(destination, value, exclusive=True)
        journal.unlink()
        return value
