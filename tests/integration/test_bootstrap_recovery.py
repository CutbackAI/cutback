"""Bootstrap retries retain exact identities and never return or persist raw scoped keys."""

import json
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from cutback.server.db import AccessKey, AuditEvent, Project, token_hash

pytestmark = pytest.mark.postgres


def key_body(role="ingest"):
    return {
        "request_id": str(uuid4()),
        "role": role,
        "environment": "staging",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "token": secrets.token_urlsafe(48),
    }


def test_project_bootstrap_reuses_its_identity_and_rejects_changed_intent(api):
    identity = uuid4()
    body = {"slug": "bootstrap-" + uuid4().hex, "environment": "staging"}
    path = f"/v1/projects?request_id={identity}"
    first = api[0].post(path, headers=api[1], json=body)
    assert first.status_code == 201
    assert first.json()["id"] == str(identity)
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 201
    same_receipt = repeated.json() == first.json()
    assert same_receipt, "BOOTSTRAP_RECEIPT_CHANGED"
    changed = api[0].post(path, headers=api[1], json={**body, "slug": "changed"})
    assert changed.status_code == 409
    with api[2].state.sessions() as session:
        assert (
            session.scalar(select(func.count()).select_from(Project).where(Project.id == identity))
            == 1
        )


def test_scoped_key_bootstrap_acknowledges_metadata_and_only_stores_a_hash(api, tenant):
    body = key_body()
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    first = api[0].post(path, headers=api[1], json=body)
    assert first.status_code == 201
    assert first.json()["id"] == body["request_id"]
    secret_absent = body["token"] not in first.text
    assert secret_absent, "RAW_TOKEN_WAS_RETURNED"
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 201
    same_receipt = repeated.json() == first.json()
    assert same_receipt, "BOOTSTRAP_RECEIPT_CHANGED"
    expected_digest = token_hash(body["token"])
    with api[2].state.sessions() as session:
        row = session.get(AccessKey, UUID(body["request_id"]))
        digest_matches = row.digest == expected_digest
        assert digest_matches, "SCOPED_KEY_DIGEST_INVALID"
        metadata = json.dumps(
            [
                event.details
                for event in session.scalars(
                    select(AuditEvent).where(AuditEvent.project_id == UUID(tenant[0]))
                )
            ]
        )
        secret_absent = body["token"] not in metadata
        assert secret_absent, "RAW_TOKEN_ENTERED_AUDIT_METADATA"


def test_scoped_key_retry_cannot_replace_a_secret_or_revive_revoked_authority(api, tenant):
    body = key_body("viewer")
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 201
    changed = api[0].post(path, headers=api[1], json={**body, "token": secrets.token_urlsafe(48)})
    assert changed.status_code == 409
    revoked = api[0].delete(f"/v1/projects/{tenant[0]}/keys/{body['request_id']}", headers=api[1])
    assert revoked.status_code == 200
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 403


@pytest.mark.parametrize("kind", ["project", "key"])
def test_bootstrap_concurrent_requests_do_not_duplicate_resources(api, tenant, kind):
    body = key_body()
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    if kind == "project":
        path = f"/v1/projects?request_id={body['request_id']}"
        body = {"slug": "concurrent-" + uuid4().hex, "environment": "staging"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api[0].post(path, headers=api[1], json=body), range(2)))
    assert [response.status_code for response in responses] == [201, 201]
    same_receipt = responses[0].json() == responses[1].json()
    assert same_receipt, "CONCURRENT_BOOTSTRAP_RECEIPT_CHANGED"


@pytest.mark.parametrize(
    "field,value",
    [("role", "editor"), ("environment", "production"), ("expires_at", "2030-01-01T00:00:00Z")],
)
def test_scoped_key_intent_is_immutable(api, tenant, field, value):
    body = key_body()
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 201
    if field == "environment":
        from cutback.server.db import Environment

        with api[2].state.sessions.begin() as session:
            session.add(Environment(project_id=UUID(tenant[0]), name="production"))
    changed = api[0].post(path, headers=api[1], json={**body, field: value})
    assert changed.status_code == 409


@pytest.mark.parametrize("role", ["ingest", "viewer", "analyst", "editor"])
def test_insufficient_roles_cannot_issue_or_bootstrap_projects(api, tenant, role):
    legacy = api[0].post(f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": role})
    assert legacy.status_code == 201
    headers = {"Authorization": "Bearer " + legacy.json()["token"]}
    response = api[0].post(
        f"/v1/projects/{tenant[0]}/scoped-keys", headers=headers, json=key_body()
    )
    assert response.status_code == 403
    response = api[0].post(
        f"/v1/projects?request_id={uuid4()}",
        headers=headers,
        json={"slug": "denied-" + uuid4().hex},
    )
    assert response.status_code == 403


def test_scoped_key_identity_and_credential_cannot_cross_projects(api, tenant):
    body = key_body()
    response = api[0].post(f"/v1/projects/{tenant[0]}/scoped-keys", headers=api[1], json=body)
    assert response.status_code == 201
    other = api[0].post("/v1/projects", headers=api[1], json={"slug": "other-" + uuid4().hex})
    assert other.status_code == 201
    path = f"/v1/projects/{other.json()['id']}/scoped-keys"
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 404
    response = api[0].post(path, headers=api[1], json={**body, "request_id": str(uuid4())})
    assert response.status_code == 409


@pytest.mark.parametrize("removed", ["receipt", "key"])
def test_missing_bootstrap_history_cannot_recreate_authority(api, tenant, removed):
    body = key_body()
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    response = api[0].post(path, headers=api[1], json=body)
    assert response.status_code == 201
    with api[2].state.sessions.begin() as session:
        if removed == "key":
            session.execute(delete(AccessKey).where(AccessKey.id == UUID(body["request_id"])))
        else:
            session.execute(
                delete(AuditEvent).where(
                    AuditEvent.project_id == UUID(tenant[0]),
                    AuditEvent.action == "scoped_key_prepared",
                )
            )
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 409


@pytest.mark.parametrize("legacy_first", [False, True])
def test_legacy_and_resumable_keys_share_quota(api, monkeypatch, legacy_first):
    from cutback.server import bootstrap_api

    monkeypatch.setattr(bootstrap_api, "MAX_PROJECT_KEYS", 1)
    project = api[0].post("/v1/projects", headers=api[1], json={"slug": "quota-" + uuid4().hex})
    assert project.status_code == 201
    base = f"/v1/projects/{project.json()['id']}"
    paths = [(base + "/scoped-keys", key_body()), (base + "/keys", {"role": "viewer"})]
    if legacy_first:
        paths.reverse()
    first = api[0].post(paths[0][0], headers=api[1], json=paths[0][1])
    assert first.status_code == 201
    second = api[0].post(paths[1][0], headers=api[1], json=paths[1][1])
    assert second.status_code == 409
    if not legacy_first:
        repeated = api[0].post(paths[0][0], headers=api[1], json=paths[0][1])
        assert repeated.status_code == 201


def test_concurrent_legacy_and_resumable_issuance_share_one_quota(api, monkeypatch):
    from cutback.server import bootstrap_api

    monkeypatch.setattr(bootstrap_api, "MAX_PROJECT_KEYS", 1)
    project = api[0].post("/v1/projects", headers=api[1], json={"slug": "quota-" + uuid4().hex})
    assert project.status_code == 201
    project_id = project.json()["id"]
    base = f"/v1/projects/{project_id}"
    requests = [(base + "/scoped-keys", key_body()), (base + "/keys", {"role": "viewer"})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda item: api[0].post(item[0], headers=api[1], json=item[1]), requests)
        )
    assert sorted(response.status_code for response in responses) == [201, 409]
    with api[2].state.sessions() as session:
        count = session.scalar(
            select(func.count())
            .select_from(AccessKey)
            .where(AccessKey.project_id == UUID(project_id))
        )
        assert count == 1


@pytest.mark.parametrize("damage", ["token", "role", "expiry", "identifier"])
def test_invalid_scoped_key_inputs_never_echo_credentials(api, tenant, damage):
    body = key_body()
    canary = "SYNTHETIC_PRIVATE_TOKEN_CANARY"
    body["token"] = canary + "x" * 30
    if damage == "token":
        body["token"] += "!"
    elif damage == "role":
        body["role"] = "admin"
    elif damage == "expiry":
        body["expires_at"] = "2030-01-01T00:00:00Z"
    else:
        body["request_id"] = "00000000-0000-1000-8000-000000000001"
    response = api[0].post(f"/v1/projects/{tenant[0]}/scoped-keys", headers=api[1], json=body)
    assert response.status_code == 422
    assert canary not in response.text


def test_deleted_project_bootstrap_cannot_recreate_the_deleted_identity(api):
    identifier = uuid4()
    body = {"slug": "delete-" + uuid4().hex}
    path = f"/v1/projects?request_id={identifier}"
    created = api[0].post(path, headers=api[1], json=body)
    assert created.status_code == 201
    deleted = api[0].delete(
        f"/v1/projects/{identifier}", headers=api[1], params={"confirm_slug": body["slug"]}
    )
    assert deleted.status_code == 200
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 409
    with api[2].state.sessions() as session:
        assert session.get(Project, identifier) is None


def root_control(api):
    from cutback.agent_control import Control

    return Control(
        "http://127.0.0.1:8080", api[1]["Authorization"].removeprefix("Bearer "), client=api[0]
    )


def test_original_local_prepare_command_recovers_connection_and_finishes_plan(
    api, tmp_path, monkeypatch
):
    from support_agent import fresh_application
    from typer.testing import CliRunner

    from cutback import agent_control, local_stack
    from cutback.cli import app
    from cutback.core.workspace import read_private
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.server.db import ExperimentRunRecord

    fresh_application(tmp_path)
    owner = str(uuid4())
    config = {
        "endpoint": "http://127.0.0.1:8080",
        "admin": api[1]["Authorization"].removeprefix("Bearer "),
        "owner": owner,
        "allow_payloads": True,
    }
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    monkeypatch.setattr(local_stack, "configured", lambda root: (root, config))
    monkeypatch.setattr(local_stack, "status", lambda root: {"status": "READY"})
    dropped = False

    class InterruptedLocalControl(agent_control.Control):
        def __init__(self, endpoint, token):
            super().__init__(endpoint, token, client=api[0])

        def __call__(self, method, path, body=None):
            nonlocal dropped
            result = super().__call__(method, path, body)
            if not dropped and method == "POST" and body.get("role") == "ingest":
                dropped = True
                raise ValueError("SYNTHETIC_COMMITTED_REPLY_LOST")
            return result

    monkeypatch.setattr(agent_control, "Control", InterruptedLocalControl)
    runner = CliRunner()
    assert runner.invoke(app, ["optimize", str(tmp_path), "--json"]).exit_code == 0
    command = [
        "workflow",
        "prepare",
        "--target",
        str(tmp_path),
        "--local",
        "--automatic",
        "--example",
        "cases.jsonl",
        "--rights-basis",
        "synthetic",
        "--rights-reference",
        "owned-bootstrap-command",
        "--max-usd",
        "4",
        "--max-requests",
        "500",
        "--minimum",
        "0.9",
        "--independent-labels",
        "--min-test-samples",
        "3",
    ]
    interrupted = runner.invoke(app, command)
    assert interrupted.exit_code == 2
    assert (
        json.loads(interrupted.stdout)["code"]
        == "BOOTSTRAP_COLLECTOR_UNAVAILABLE_RESUME_SAVED_INTENT"
    )
    completed = runner.invoke(app, command)
    assert completed.exit_code == 0
    state = json.loads(completed.stdout)
    assert state["phase"] == "PLAN_READY"
    assert state["plan"]["paid_calls_made"] == 0
    saved = read_private(tmp_path / ".cutback/agent/connection.json")
    no_credentials = all(
        token not in interrupted.stdout + completed.stdout
        for token in (config["admin"], saved["token"], saved["ingest_token"])
    )
    assert no_credentials, "BOOTSTRAP_COMMAND_EXPOSED_CREDENTIALS"
    with api[2].state.sessions() as session:
        project_id = UUID(saved["project"])
        keys = session.scalar(
            select(func.count()).select_from(AccessKey).where(AccessKey.project_id == project_id)
        )
        runs = session.scalar(
            select(func.count())
            .select_from(ExperimentRunRecord)
            .where(ExperimentRunRecord.project_id == project_id)
        )
        assert keys == 2 and runs == 0


@pytest.mark.parametrize("lost", ["project", "project_admin", "ingest"])
def test_agent_bootstrap_recovers_lost_commit_acknowledgements_without_new_keys(
    api, tmp_path, lost
):
    from cutback.agent_bootstrap import bootstrap_connection
    from cutback.core.workspace import read_private

    control = root_control(api)
    owner = str(uuid4())
    calls = []
    dropped = False

    class LostAcknowledgement:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            nonlocal dropped
            calls.append((method, path))
            if method == "POST":
                # Credentials and all identities must exist before the first write.
                assert (tmp_path / ".cutback/agent/bootstrap-credentials.json").is_file()
            result = control(method, path, body)
            if (
                not dropped
                and method == "POST"
                and (
                    lost == "project"
                    and path.startswith("/v1/projects?")
                    or lost != "project"
                    and body.get("role") == lost
                )
            ):
                dropped = True
                raise ValueError("COLLECTOR_RESPONSE_UNCERTAIN_INSPECT_SAVED_STATE")
            return result

    with pytest.raises(ValueError, match="RESUME_SAVED_INTENT"):
        bootstrap_connection(tmp_path, LostAcknowledgement(), owner=owner)
    pending = read_private(tmp_path / ".cutback/agent/bootstrap-credentials.json")
    saved = bootstrap_connection(tmp_path, LostAcknowledgement(), owner=owner)
    assert saved["project"] == pending["project"]["id"]
    same_credentials = (
        saved["token"] == pending["keys"]["project_admin"]["token"]
        and saved["ingest_token"] == pending["keys"]["ingest"]["token"]
    )
    assert same_credentials, "BOOTSTRAP_REPLACED_SAVED_CREDENTIALS"
    assert saved["bootstrap_receipt"]["expires_at"] == pending["expires_at"]
    assert not (tmp_path / ".cutback/agent/bootstrap-credentials.json").exists()
    before = len(calls)
    repeated = bootstrap_connection(tmp_path, LostAcknowledgement(), owner=owner)
    same_connection = repeated == saved
    assert same_connection, "SAVED_BOOTSTRAP_CONNECTION_CHANGED"
    assert len(calls) == before
    assert not any(
        "experiments" in path or "approve" in path or "datasets" in path for _, path in calls
    )
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AccessKey)
                .where(AccessKey.project_id == UUID(saved["project"]))
            )
            == 2
        )


@pytest.mark.parametrize("resource", ["project", "key"])
def test_untrusted_bootstrap_receipt_stops_retries_without_persisting_content(
    api, tmp_path, resource
):
    from cutback.agent_bootstrap import bootstrap_connection

    control = root_control(api)
    owner = str(uuid4())
    calls = []
    canary = "SYNTHETIC_UNTRUSTED_PRIVATE_RECEIPT_CANARY"

    class ContradictoryCollector:
        endpoint = control.endpoint

        def __call__(self, method, path, body=None):
            calls.append((method, path))
            result = control(method, path, body)
            if method == "POST" and (
                resource == "project"
                and path.startswith("/v1/projects?")
                or resource == "key"
                and path.endswith("/scoped-keys")
            ):
                return {**result, "id": canary, "token": canary}
            return result

    with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
        bootstrap_connection(tmp_path, ContradictoryCollector(), owner=owner)
    assert canary not in (tmp_path / ".cutback/agent/bootstrap-credentials.json").read_text()
    before = len(calls)
    with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
        bootstrap_connection(tmp_path, ContradictoryCollector(), owner=owner)
    assert len(calls) == before


def test_expired_scoped_key_replay_never_renews_expiry(api, tenant, monkeypatch):
    from cutback.server import bootstrap_api

    body = key_body()
    path = f"/v1/projects/{tenant[0]}/scoped-keys"
    first = api[0].post(path, headers=api[1], json=body)
    assert first.status_code == 201

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=2)

    monkeypatch.setattr(bootstrap_api, "datetime", Future)
    repeated = api[0].post(path, headers=api[1], json=body)
    assert repeated.status_code == 403
    with api[2].state.sessions() as session:
        assert session.get(
            AccessKey, UUID(body["request_id"])
        ).expires_at == datetime.fromisoformat(body["expires_at"])


@pytest.mark.parametrize("field", ["endpoint", "project", "token", "ingest_token", "key_id"])
def test_completed_connection_rechecks_its_original_intent_before_network(api, tmp_path, field):
    from cutback.agent_bootstrap import bootstrap_connection, validate_connection

    owner = str(uuid4())
    saved = bootstrap_connection(tmp_path, root_control(api), owner=owner)
    # This check is offline; no changed destination receives either credential.
    if field == "endpoint":
        saved[field] = "http://127.0.0.1:8099"
    elif field == "project":
        saved[field] = str(uuid4())
    elif field == "key_id":
        saved["bootstrap_receipt"]["key_ids"]["ingest"] = str(uuid4())
    else:
        saved[field] = secrets.token_urlsafe(48)
    with pytest.raises(ValueError, match="DESTINATION_CHANGED"):
        validate_connection(tmp_path, saved, owner=owner)


def test_concurrent_duplicate_credential_cannot_cross_project_scope(api):
    projects = []
    for _ in range(2):
        response = api[0].post(
            "/v1/projects", headers=api[1], json={"slug": "credential-scope-" + uuid4().hex}
        )
        assert response.status_code == 201
        projects.append(response.json()["id"])
    body = key_body()
    requests = [(project, {**body, "request_id": str(uuid4())}) for project in projects]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda item: api[0].post(
                    f"/v1/projects/{item[0]}/scoped-keys", headers=api[1], json=item[1]
                ),
                requests,
            )
        )
    assert sorted(response.status_code for response in responses) == [201, 409]


def test_oidc_project_replay_rechecks_current_organization_authority(api, monkeypatch):
    from cutback.server.db import Organization

    with api[2].state.sessions.begin() as session:
        own = Organization(name="Synthetic authorized organization")
        other = Organization(name="Synthetic unrelated organization")
        session.add_all([own, other])
        session.flush()
        own_id, other_id = own.id, other.id
    denied_id = uuid4()
    created = api[0].post(
        f"/v1/projects?request_id={denied_id}",
        headers=api[1],
        json={"slug": "other-org-" + uuid4().hex, "organization_id": str(other_id)},
    )
    assert created.status_code == 201

    class Verifier:
        organizations = {own_id: "owner"}

        def principal(self, token, sessions):
            return {
                "role": "oidc",
                "actor": "synthetic-organization-owner",
                "organizations": self.organizations,
                "project_roles": {},
            }

    verifier = Verifier()
    monkeypatch.setattr(api[2].state, "oidc_verifier", verifier)
    headers = {"Authorization": "Bearer synthetic.header.signature"}
    body = {"slug": "owned-org-" + uuid4().hex, "organization_id": str(own_id)}
    hidden = api[0].post(f"/v1/projects?request_id={denied_id}", headers=headers, json=body)
    assert hidden.status_code == 404
    identifier = uuid4()
    first = api[0].post(f"/v1/projects?request_id={identifier}", headers=headers, json=body)
    assert first.status_code == 201
    verifier.organizations = {}
    replay = api[0].post(f"/v1/projects?request_id={identifier}", headers=headers, json=body)
    assert replay.status_code == 403
