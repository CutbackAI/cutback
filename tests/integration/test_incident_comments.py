"""Real PostgreSQL collaboration: encrypted bodies, current scope and immutable revisions."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from support_incident_comments import collaboration as collaboration
from test_identity_teams import identity as identity

from cutback.server.comment_db import IncidentComment, IncidentCommentVersion
from cutback.server.db import AuditEvent, CapturePolicyRecord, PayloadSample

pytestmark = pytest.mark.postgres
TEXT = "OWNED_SENSITIVE_COMMENT_ALPHA"


def draft(collaboration, **extra):
    return {
        "version_id": str(uuid4()),
        "expected_revision": 0,
        "policy_id": collaboration["policy"],
        "text": TEXT,
        **extra,
    }


def write(api, collaboration, body=None, *, identifier=None, headers=None):
    identifier = identifier or str(uuid4())
    body = body or draft(collaboration)
    response = api[0].put(
        collaboration["path"] + "/" + identifier, headers=headers or api[1], json=body
    )
    assert response.status_code == 201, response.text
    return response.json(), identifier, body


def read(api, collaboration, identifier, *, headers=None, revision=None):
    return api[0].post(
        collaboration["path"] + "/" + identifier + "/content",
        params={"revision": revision} if revision else {},
        headers=headers or api[1],
        json={"purpose": "owned-triage-review"},
    )


def test_comment_metadata_default_explicit_content_encryption_and_audit(
    api, tenant, collaboration, monkeypatch
):
    client, admin, app = api
    identifier, body = str(uuid4()), draft(collaboration)
    path = collaboration["path"] + "/" + identifier
    assert client.put(path, headers=tenant[2], json=body).status_code == 403
    assert client.get(collaboration["path"], headers=tenant[1]).status_code == 403
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    assert client.put(path, headers=admin, json=body).status_code == 403
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    created, _, _ = write(api, collaboration, body, identifier=identifier)
    assert created["accepted_revision"] == 1 and not created["duplicate"]
    metadata = client.get(collaboration["path"], headers=tenant[2])
    assert metadata.status_code == 200 and not metadata.json()["content_included"]
    assert TEXT not in metadata.text
    contents = read(api, collaboration, identifier, headers=tenant[2])
    assert contents.status_code == 200 and contents.json()["text"] == TEXT
    assert contents.json()["evidence"] == "human_comment_not_application_truth"
    assert client.post(path + "/content", headers=tenant[2], json={}).status_code == 422
    assert all(
        TEXT.encode() not in p.read_bytes()
        for p in collaboration["directory"].rglob("*")
        if p.is_file()
    )
    exported = client.get(f"/v1/projects/{tenant[0]}/export", headers=admin)
    assert exported.status_code == 200 and TEXT not in exported.text
    assert '"table": "incident_comments"' in exported.text
    assert '"table": "incident_comment_versions"' in exported.text
    with app.state.sessions() as session:
        audits = session.scalars(
            select(AuditEvent).where(AuditEvent.project_id == collaboration["project"])
        ).all()
        assert TEXT not in json.dumps([row.details for row in audits])
        assert any(row.action == "incident_comment_read" for row in audits)
        payload = session.get(PayloadSample, UUID(body["version_id"]))
        assert TEXT not in json.dumps(payload.metadata_safe)


def test_concurrent_revisions_reconcile_same_intent_and_preserve_old_body(api, collaboration):
    created, identifier, original = write(api, collaboration)
    path = collaboration["path"] + "/" + identifier
    duplicate, _, _ = write(api, collaboration, original, identifier=identifier)
    assert duplicate["duplicate"]
    bodies = [
        draft(collaboration, expected_revision=1, text=TEXT + suffix) for suffix in ("_B", "_C")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda value: api[0].put(path, headers=api[1], json=value), bodies)
        )
    assert sorted(row.status_code for row in responses) == [201, 409]
    winner = bodies[next(i for i, value in enumerate(responses) if value.status_code == 201)]
    replay, _, _ = write(api, collaboration, winner, identifier=identifier)
    assert replay["duplicate"] and replay["accepted_revision"] == 2
    assert api[0].put(path, headers=api[1], json={**winner, "text": "different"}).status_code == 409
    assert read(api, collaboration, identifier, revision=1).json()["text"] == TEXT
    assert read(api, collaboration, identifier).json()["text"] == winner["text"]
    versions = api[0].get(path + "/versions", headers=api[1]).json()
    assert [row["revision"] for row in versions["items"]] == [2, 1]
    assert TEXT not in json.dumps(versions)


def test_current_author_scope_revocation_and_admin_erasure_after_policy_revocation(
    api, tenant, collaboration, monkeypatch
):
    project = tenant[0]
    keys = [
        api[0].post(f"/v1/projects/{project}/keys", headers=api[1], json={"role": "editor"}).json()
        for _ in range(2)
    ]
    authors = [{"Authorization": "Bearer " + key["token"]} for key in keys]
    _, identifier, _ = write(api, collaboration, headers=authors[0])
    path = collaboration["path"] + "/" + identifier
    assert (
        api[0]
        .put(path, headers=authors[1], json=draft(collaboration, expected_revision=1))
        .status_code
        == 403
    )
    assert (
        api[0].post(path + "/delete", headers=authors[1], json={"expected_revision": 1}).status_code
        == 403
    )
    assert (
        api[0]
        .delete(f"/v1/projects/{project}/keys/{keys[0]['key_id']}", headers=api[1])
        .status_code
        == 200
    )
    assert api[0].get(collaboration["path"], headers=authors[0]).status_code == 401
    with api[2].state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(collaboration["policy"])).enabled = False
    assert read(api, collaboration, identifier, headers=tenant[2]).status_code == 403
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    erased = api[0].post(path + "/delete", headers=api[1], json={"expected_revision": 1})
    assert erased.status_code == 200 and erased.json()["comment"]["deleted"]
    assert not any(p.is_file() for p in collaboration["directory"].rglob("*"))
    assert (
        api[0]
        .post(path + "/delete", headers=api[1], json={"expected_revision": 1})
        .json()["duplicate"]
    )
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    assert read(api, collaboration, identifier).status_code == 410
    assert api[0].get(collaboration["path"], headers=tenant[2]).json()["items"][0]["deleted"]


@pytest.mark.parametrize("change", ["workflow", "environment", "provider", "sampling", "redaction"])
def test_comment_policy_scope_is_exact_and_no_rejected_body_is_stored(api, collaboration, change):
    with api[2].state.sessions.begin() as session:
        row = session.get(CapturePolicyRecord, UUID(collaboration["policy"]))
        policy = dict(row.policy)
        field, value = {
            "workflow": ("workflows", ["other"]),
            "environment": ("environments", ["production"]),
            "provider": ("allowed_providers", ["openai"]),
            "sampling": ("sample_rate", 0.5),
            "redaction": ("never_capture_fields", ["text"]),
        }[change]
        row.policy = {**policy, field: value}
    response = api[0].put(
        collaboration["path"] + "/" + str(uuid4()), headers=api[1], json=draft(collaboration)
    )
    assert response.status_code == (422 if change == "redaction" else 403)
    assert not any(p.is_file() for p in collaboration["directory"].rglob("*"))


def test_shared_capture_quota_serializes_concurrent_comment_writes(api, collaboration):
    with api[2].state.sessions.begin() as session:
        row = session.get(CapturePolicyRecord, UUID(collaboration["policy"]))
        row.policy = {**row.policy, "max_samples": 1}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: api[0].put(
                    collaboration["path"] + "/" + str(uuid4()),
                    headers=api[1],
                    json=draft(collaboration),
                ),
                range(2),
            )
        )
    assert sorted(row.status_code for row in responses) == [201, 429]
    assert len(api[0].get(collaboration["path"], headers=api[1]).json()["items"]) == 1


def test_cross_project_parent_database_constraint_and_cursor_scope(api, tenant, collaboration):
    _, identifier, _ = write(api, collaboration)
    foreign = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "independent-" + uuid4().hex})
        .json()["id"]
    )
    foreign_path = collaboration["path"].replace(tenant[0], foreign)
    assert api[0].get(foreign_path, headers=api[1]).status_code == 404
    assert api[0].get(foreign_path, headers=tenant[2]).status_code == 403
    assert (
        api[0].get(collaboration["path"] + "?after=" + str(uuid4()), headers=api[1]).status_code
        == 404
    )
    now = datetime.now(UTC)
    with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
        session.add(
            IncidentComment(
                id=uuid4(),
                project_id=UUID(foreign),
                incident_id=collaboration["incident"],
                creator="synthetic",
                revision=1,
                current_version_id=uuid4(),
                updated_at=now,
            )
        )


@pytest.mark.parametrize("lost_ack", [False, True])
def test_comment_storage_cleanup_distinguishes_rollback_from_lost_commit_ack(
    api, collaboration, lost_ack
):
    identifier, body = str(uuid4()), draft(collaboration)
    payload_id = UUID(body["version_id"])
    fired = []

    def mark(session, context, instances):
        if any(
            isinstance(row, IncidentCommentVersion) and row.id == payload_id for row in session.new
        ):
            if not lost_ack:
                raise ValueError("OWNED_COMMENT_INSERT_FAILED")
            session.info["owned_comment_commit"] = True

    def after(session):
        if session.info.get("owned_comment_commit") and not fired:
            fired.append(True)
            raise ValueError("OWNED_COMMENT_COMMIT_ACK_LOST")

    event.listen(Session, "before_flush", mark)
    event.listen(Session, "after_commit", after)
    try:
        response = api[0].put(collaboration["path"] + "/" + identifier, headers=api[1], json=body)
        assert response.status_code == 500
    finally:
        event.remove(Session, "before_flush", mark)
        event.remove(Session, "after_commit", after)
    files = [p for p in collaboration["directory"].rglob("*") if p.is_file()]
    if lost_ack:
        assert files and read(api, collaboration, identifier).status_code == 200
        assert write(api, collaboration, body, identifier=identifier)[0]["duplicate"]
    else:
        assert not files
        with api[2].state.sessions() as session:
            assert session.get(IncidentComment, UUID(identifier)) is None


def test_comment_expiry_and_project_deletion_cover_versions_and_encrypted_objects(
    api, tenant, collaboration
):
    from cutback.server.worker import expire_payloads

    _, identifier, body = write(api, collaboration)
    with api[2].state.sessions.begin() as session:
        session.get(PayloadSample, UUID(body["version_id"])).expires_at = datetime.now(
            UTC
        ) - timedelta(seconds=1)
    assert read(api, collaboration, identifier).status_code == 410
    expire_payloads(api[2].state.sessions, api[2].state.artifact_store)
    assert not any(p.is_file() for p in collaboration["directory"].rglob("*"))
    slug = api[0].get(f"/v1/projects/{tenant[0]}", headers=api[1]).json()["slug"]
    deleted = api[0].delete(
        f"/v1/projects/{tenant[0]}", headers=api[1], params={"confirm_slug": slug}
    )
    assert deleted.status_code == 200
    with api[2].state.sessions() as session:
        for model in (IncidentComment, IncidentCommentVersion):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.project_id == collaboration["project"])
                )
                == 0
            )


def test_comment_pagination_current_policy_choices_and_metadata_permissions(
    api, tenant, collaboration
):
    client, admin, _ = api
    choices = client.get(collaboration["path"] + "/policies", headers=admin)
    assert choices.status_code == 200 and choices.json()["environment"] == "staging"
    assert choices.json()["items"][0]["id"] == collaboration["policy"]
    assert client.get(collaboration["path"] + "/policies", headers=tenant[2]).status_code == 403
    identifiers = [write(api, collaboration)[1] for _ in range(3)]
    seen, after = [], ""
    for _ in range(3):
        result = client.get(
            collaboration["path"],
            headers=admin,
            params={"limit": 1, **({"after": after} if after else {})},
        ).json()
        assert len(result["items"]) == 1 and result["items"][0]["can_edit"]
        seen.append(result["items"][0]["id"])
        after = result["next_cursor"]
    assert seen == identifiers and after is None
    for item in client.get(collaboration["path"], headers=tenant[2]).json()["items"]:
        assert not item["can_edit"] and not item["can_delete"]
    assert (
        client.get(collaboration["path"], headers=admin, params={"limit": 101}).status_code == 422
    )


def test_comment_storage_outage_does_not_claim_erasure_or_expose_error_details(
    api, collaboration, monkeypatch
):
    _, identifier, _ = write(api, collaboration)

    def outage(*args):
        raise OSError(TEXT)

    with monkeypatch.context() as patch:
        patch.setattr(api[2].state.artifact_store, "delete", outage)
        response = api[0].post(
            collaboration["path"] + "/" + identifier + "/delete",
            headers=api[1],
            json={"expected_revision": 1},
        )
        assert response.status_code == 503 and TEXT not in response.text
    assert read(api, collaboration, identifier).status_code == 200
    assert not api[0].get(collaboration["path"], headers=api[1]).json()["items"][0]["deleted"]


def test_populated_comment_downgrade_refuses_until_audited_project_erasure(
    isolated_postgres_url, tmp_path, monkeypatch
):
    import secrets
    from importlib.resources import files

    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from support_incident_comments import prepare_collaboration

    from cutback.server.api import create_app

    admin = {"Authorization": "Bearer " + secrets.token_urlsafe(48)}
    app = create_app(
        database_url=isolated_postgres_url,
        admin_token=admin["Authorization"][7:],
        allow_insecure_http=True,
    )
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    with TestClient(app) as client:
        project = client.post(
            "/v1/projects", headers=admin, json={"slug": "synthetic-comments-migration"}
        ).json()
        owned = (client, admin, app)
        data = prepare_collaboration(owned, (project["id"], {}, {}), tmp_path, monkeypatch)
        write(owned, data)
        with pytest.raises(
            ValueError, match="INCIDENT_COMMENTS_EXPORT_AND_REMOVE_BEFORE_DOWNGRADE"
        ):
            command.downgrade(cfg, "0035")
        assert client.get("/v1/health/ready", headers=admin).json()["schema_revision"] == "0036"
        assert (
            client.delete(
                f"/v1/projects/{project['id']}",
                headers=admin,
                params={"confirm_slug": project["slug"]},
            ).status_code
            == 200
        )
        command.downgrade(cfg, "0035")
        command.upgrade(cfg, "head")
        command.check(cfg)


@pytest.mark.parametrize("lost_reply", [None, "create", "revise", "erase"])
def test_independent_comment_sample_reconciles_lost_replies_without_recreating_text(
    api, collaboration, tmp_path, lost_reply
):
    import importlib.util
    from pathlib import Path

    import httpx

    script = Path(__file__).resolve().parents[2] / "examples/incident-collaboration/sample.py"
    spec = importlib.util.spec_from_file_location("owned_comment_example", script)
    sample = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sample)
    writes, lost = [], []

    def send(request):
        assert request.url.host == "127.0.0.1" and request.url.port == 9
        result = api[0].request(
            request.method,
            request.url.path + ("?" + request.url.query.decode() if request.url.query else ""),
            headers=dict(request.headers),
            content=request.content,
        )
        phase = (
            "create"
            if request.method == "PUT" and json.loads(request.content)["expected_revision"] == 0
            else "revise"
            if request.method == "PUT"
            else "erase"
            if request.method == "POST" and request.url.path.endswith("/delete")
            else None
        )
        if phase:
            writes.append(phase)
        if phase == lost_reply and phase is not None and not lost:
            lost.append(True)
            raise httpx.ReadTimeout("OWNED_COMMENT_REPLY_LOST", request=request)
        return httpx.Response(
            result.status_code, content=result.content, headers={"Content-Type": "application/json"}
        )

    receipt = tmp_path / "private/sample.json"
    arguments = (
        "http://127.0.0.1:9",
        str(collaboration["project"]),
        str(collaboration["incident"]),
        collaboration["policy"],
        api[1]["Authorization"][7:],
        receipt,
    )
    preview = sample.demonstrate(*arguments)
    assert preview["network_requests"] == 0 and not receipt.exists()
    with pytest.raises(ValueError, match="OWNED_NUMERIC_LOOPBACK"):
        sample.demonstrate("https://example.invalid", *arguments[1:])
    if lost_reply:
        with pytest.raises(ValueError, match="RESPONSE_UNCERTAIN_RESUME_SAME_JOURNAL"):
            sample.demonstrate(*arguments, execute_local=True, transport=httpx.MockTransport(send))
    completed = sample.demonstrate(
        *arguments, execute_local=True, transport=httpx.MockTransport(send)
    )
    assert completed["status"] == "COMPLETED_SYNTHETIC_COMMENT_LIFECYCLE"
    before = list(writes)
    sample.demonstrate(*arguments, execute_local=True, transport=httpx.MockTransport(send))
    assert writes == before and completed["retained_text_revisions"] == 0
    raw = receipt.read_text()
    assert (
        api[1]["Authorization"][7:] not in raw
        and sample.TEXT not in raw
        and sample.REVISION not in raw
    )


def test_comment_oidc_grants_are_live_and_token_role_cannot_override_revocation(
    api, identity, tmp_path, monkeypatch
):
    from support_incident_comments import prepare_collaboration

    verifier, token = identity
    api[2].state.oidc_verifier = verifier
    org = (
        api[0]
        .post("/v1/organizations", headers=api[1], json={"name": "Synthetic incident team"})
        .json()["id"]
    )
    project = (
        api[0]
        .post(
            "/v1/projects",
            headers=api[1],
            json={"slug": "synthetic-oidc-comments-" + uuid4().hex, "organization_id": org},
        )
        .json()["id"]
    )
    data = prepare_collaboration(api, (project, {}, {}), tmp_path, monkeypatch)
    _, original, _ = write(api, data)
    member = (
        api[0]
        .post(
            f"/v1/organizations/{org}/members",
            headers=api[1],
            json={"issuer": verifier.issuer, "subject": "synthetic-user"},
        )
        .json()["id"]
    )
    grant = f"/v1/organizations/{org}/members/{member}/grants"
    user = {"Authorization": "Bearer " + token(role="admin")}
    assert (
        api[0]
        .post(grant, headers=api[1], json={"project_id": project, "role": "viewer"})
        .status_code
        == 200
    )
    assert read(api, data, original, headers=user).status_code == 200
    assert (
        api[0].put(data["path"] + "/" + str(uuid4()), headers=user, json=draft(data)).status_code
        == 403
    )
    assert (
        api[0]
        .post(grant, headers=api[1], json={"project_id": project, "role": "editor"})
        .status_code
        == 200
    )
    _, identifier, _ = write(api, data, headers=user)
    metadata = api[0].get(data["path"], headers=user).json()["items"]
    assert next(row for row in metadata if row["id"] == identifier)["can_edit"]
    assert (
        api[0]
        .post(grant, headers=api[1], json={"project_id": project, "role": "viewer"})
        .status_code
        == 200
    )
    assert not next(
        row
        for row in api[0].get(data["path"], headers=user).json()["items"]
        if row["id"] == identifier
    )["can_edit"]
    assert (
        api[0]
        .put(data["path"] + "/" + identifier, headers=user, json=draft(data, expected_revision=1))
        .status_code
        == 403
    )
    assert (
        api[0].delete(f"/v1/organizations/{org}/members/{member}", headers=api[1]).status_code
        == 200
    )
    assert read(api, data, original, headers=user).status_code == 401
