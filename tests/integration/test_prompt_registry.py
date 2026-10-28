from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from support_evaluation import authorized_dataset

from cutback.core.prompts import PromptDocument, PromptGraph, fingerprint
from cutback.server.prompt_db import PromptVersion

pytestmark = pytest.mark.postgres


@pytest.fixture
def prompt_setup(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    base, _, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    credentials = []
    for role in ("analyst", "prompt_reader"):
        response = client.post(
            base + "/keys", headers=admin, json={"role": role, "environment": "staging"}
        )
        assert response.status_code == 201, response.text
        key = response.json()
        credentials.append((key["key_id"], {"Authorization": "Bearer " + key["token"]}))
    return base, dataset["provenance"]["policy_id"], credentials


def create_prompt(client, headers, base, key="synthetic-prompt"):
    response = client.post(
        base + "/prompts", headers=headers, json={"key": key, "workflow": "classify"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def version_body(policy, document=None, **changes):
    return {
        "version_id": str(uuid4()),
        "expected_latest_ordinal": 0,
        "policy_id": policy,
        "environment": "staging",
        "provenance_ref": "owned-synthetic-prompt",
        "document": document
        or {"text": "OWNED_PRIVATE_INSTRUCTION: {{input}}", "variables": {"input": "string"}},
        **changes,
    }


def post_version(client, headers, base, prompt, body):
    return client.post(base + "/prompts/" + prompt["id"] + "/versions", headers=headers, json=body)


def context(**changes):
    return {
        "purpose": "owned-synthetic-preview",
        "environment": "staging",
        "workflow": "classify",
        **changes,
    }


def give_grant(client, admin, base, prompt, policy, actor, **changes):
    response = client.post(
        base + "/prompts/" + prompt["id"] + "/read-grants",
        headers=admin,
        json={
            "actor": actor,
            "environment": "staging",
            "workflow": "classify",
            "policy_id": policy,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "approval_ref": "synthetic-scoped-reader",
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_version_cas_immutability_privacy_and_purpose_bound_preview(api, tenant, prompt_setup):
    client, admin, app = api
    base, policy, creds = prompt_setup
    author = creds[0][1]
    prompt = create_prompt(client, author, base)
    body = version_body(policy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: post_version(client, author, base, prompt, body), range(2))
        )
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert {r.json()["id"] for r in results} == {body["version_id"]}
    version = results[0].json()
    assert version["ordinal"] == 1 and "OWNED_PRIVATE_INSTRUCTION" not in str(version)
    assert version["document_hash"] != fingerprint(
        PromptDocument.model_validate(body["document"]).model_dump(mode="json")
    )
    changed = {**body, "document": {"text": "changed {{input}}", "variables": {"input": "string"}}}
    assert post_version(client, author, base, prompt, changed).status_code == 409
    assert (
        post_version(client, author, base, prompt, {**body, "version_id": str(uuid4())}).status_code
        == 409
    )
    path = base + "/prompts/" + prompt["id"] + "/versions/" + version["id"]
    for headers in (tenant[1], tenant[2], creds[1][1]):
        assert client.post(path + "/content", headers=headers, json=context()).status_code == 403
    preview = client.post(
        path + "/preview",
        headers=author,
        json=context(variables={"input": "UNRETAINED_PREVIEW_VALUE"}, values_authorized=True),
    )
    assert preview.status_code == 200, preview.text
    assert (
        preview.json()["rendered"]["text"] == "OWNED_PRIVATE_INSTRUCTION: UNRETAINED_PREVIEW_VALUE"
    )
    assert preview.json()["values_retained"] is False and preview.json()["provider_calls"] == 0
    assert (
        client.post(
            path + "/preview",
            headers=author,
            json=context(variables={"input": "test"}, values_authorized=False),
        ).status_code
        == 422
    )
    metadata = client.get(base + "/prompts/" + prompt["id"] + "/versions", headers=tenant[2])
    assert metadata.status_code == 200 and "OWNED_PRIVATE_INSTRUCTION" not in metadata.text
    content = client.post(path + "/content", headers=author, json=context()).json()
    receipt = content["content_receipts"][version["id"]]
    assert (
        fingerprint({"document": content["documents"][version["id"]], **receipt})
        == version["document_hash"]
    )
    export = client.get(base + "/export", headers=admin)
    for forbidden in (
        "OWNED_PRIVATE_INSTRUCTION",
        "UNRETAINED_PREVIEW_VALUE",
        receipt["receipt_nonce"],
        receipt["request_fingerprint"],
    ):
        assert forbidden not in export.text
    assert not any(
        b"UNRETAINED_PREVIEW_VALUE" in p.read_bytes()
        or b"OWNED_PRIVATE_INSTRUCTION" in p.read_bytes()
        for p in app.state.artifact_store.root.rglob("*")
        if p.is_file()
    )


def test_every_dependency_requires_live_content_grant_and_vendor_scope(api, tenant, prompt_setup):
    client, admin, app = api
    base, policy, creds = prompt_setup
    author_id, author = creds[0]
    reader_id, reader = creds[1]
    component = create_prompt(client, admin, base, "synthetic-component")
    component_body = version_body(policy, {"text": "OWNED_COMPONENT_POLICY. "})
    component_version = post_version(client, admin, base, component, component_body).json()
    prompt = create_prompt(client, author, base)
    body = version_body(
        policy,
        {
            "text": "{{>rules}}Classify {{input}}",
            "variables": {"input": "string"},
            "dependencies": {"rules": component_version["id"]},
        },
    )
    assert post_version(client, author, base, prompt, body).status_code == 403
    author_grant = give_grant(client, admin, base, component, policy, author_id)
    version_response = post_version(client, author, base, prompt, body)
    assert version_response.status_code == 201, version_response.text
    version = version_response.json()
    resolve = base + "/prompts/resolve/" + prompt["key"]
    request = context(provider="local", version_id=version["id"])
    assert client.post(resolve, headers=reader, json=request).status_code == 403
    give_grant(client, admin, base, prompt, policy, reader_id)
    assert client.post(resolve, headers=reader, json=request).status_code == 403
    component_grant = give_grant(client, admin, base, component, policy, reader_id)
    response = client.post(resolve, headers=reader, json=request)
    assert response.status_code == 200, response.text
    graph = response.json()
    assert graph["provider_calls"] == 0 and graph["variables_uploaded"] is False
    loaded = PromptGraph(
        UUID(graph["root_version_id"]),
        {
            UUID(key): PromptDocument.model_validate(value)
            for key, value in graph["documents"].items()
        },
    )
    assert (
        loaded.render({"input": "LOCALLY_RENDERED_VALUE"})["text"]
        == "OWNED_COMPONENT_POLICY. Classify LOCALLY_RENDERED_VALUE"
    )
    for endpoint in (base + "/datasets", base + "/prompts", base + "/capture-policies"):
        assert client.get(endpoint, headers=reader).status_code == 403
    assert (
        client.post(resolve, headers=reader, json={**request, "provider": "anthropic"}).status_code
        == 403
    )
    assert (
        client.post(resolve, headers=reader, json={**request, "workflow": "other"}).status_code
        == 403
    )
    assert (
        client.delete(
            base + "/prompt-read-grants/" + component_grant["id"], headers=admin
        ).status_code
        == 200
    )
    assert client.post(resolve, headers=reader, json=request).status_code == 403
    assert (
        client.delete(base + "/prompt-read-grants/" + author_grant["id"], headers=admin).status_code
        == 200
    )
    assert (
        client.post(
            base + "/prompts/" + prompt["id"] + "/versions/" + version["id"] + "/content",
            headers=author,
            json=context(),
        ).status_code
        == 403
    )
    assert "LOCALLY_RENDERED_VALUE" not in client.get(base + "/export", headers=admin).text


def test_concurrent_distinct_versions_cannot_overwrite_same_head_and_invalid_schema_does_not_write(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, _ = prompt_setup
    prompt = create_prompt(client, admin, base)
    bodies = [version_body(policy) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda b: post_version(client, admin, base, prompt, b), bodies))
    assert sorted(r.status_code for r in responses) == [201, 409]
    before = {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()}
    invalid = version_body(
        policy,
        {"text": "constant", "output_schema": {"type": "not-a-type"}},
        expected_latest_ordinal=1,
    )
    assert post_version(client, admin, base, prompt, invalid).status_code == 422
    cycle = version_body(policy, {"text": "constant"}, expected_latest_ordinal=1)
    cycle["document"] = {"text": "{{>self}}", "dependencies": {"self": cycle["version_id"]}}
    assert post_version(client, admin, base, prompt, cycle).status_code == 422
    assert {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()} == before
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PromptVersion)
                .where(PromptVersion.project_id == UUID(tenant[0]))
            )
            == 1
        )


def test_failed_prompt_sql_commit_cleans_new_content_and_keeps_retry_unspent(
    api, tenant, prompt_setup
):
    client, admin, app = api
    base, policy, _ = prompt_setup
    prompt = create_prompt(client, admin, base)
    before = {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()}
    body = version_body(policy)

    def fail_commit(session):
        if any(
            isinstance(row, PromptVersion) and row.id == UUID(body["version_id"])
            for row in session.identity_map.values()
        ):
            raise RuntimeError("private-prompt-sql-failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        response = post_version(client, admin, base, prompt, body)
        assert response.status_code == 500 and "private-prompt-sql-failure" not in response.text
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()} == before
    with app.state.sessions() as session:
        assert not session.get(PromptVersion, UUID(body["version_id"]))
    assert post_version(client, admin, base, prompt, body).status_code == 201


def test_alias_promotion_concurrency_exact_rollback_and_purpose_bound_diff(
    api, tenant, prompt_setup
):
    client, admin, _ = api
    base, policy, creds = prompt_setup
    prompt = create_prompt(client, admin, base)
    first = post_version(client, admin, base, prompt, version_body(policy)).json()
    second_body = version_body(
        policy,
        {"text": "REVISED_PRIVATE_INSTRUCTION: {{input}}", "variables": {"input": "string"}},
        expected_latest_ordinal=1,
    )
    second = post_version(client, admin, base, prompt, second_body).json()
    path = base + "/prompts/" + prompt["id"] + "/aliases"
    initial = {
        "version_id": first["id"],
        "environment": "staging",
        "alias": "staging",
        "expected_revision": 0,
        "approval_ref": "synthetic-manual-v1",
        "quality_unverified_acknowledged": True,
    }
    assert client.post(path, headers=creds[0][1], json=initial).status_code == 403
    assert (
        client.post(
            path, headers=admin, json={**initial, "quality_unverified_acknowledged": False}
        ).status_code
        == 422
    )
    response = client.post(path, headers=admin, json=initial)
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert (
        receipt["alias"]["revision"] == 1
        and receipt["receipt"]["quality_status"] == "manual_quality_unverified"
    )
    assert client.post(path, headers=admin, json=initial).json()["status"] == "duplicate"
    next_request = {
        **initial,
        "version_id": second["id"],
        "expected_revision": 1,
        "expected_version_id": first["id"],
        "approval_ref": "synthetic-manual-v2",
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda n: client.post(
                    path,
                    headers=admin,
                    json={**next_request, "approval_ref": "synthetic-approval-" + str(n)},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in results) == [201, 409]
    changed = next(r.json() for r in results if r.status_code == 201)
    assert changed["alias"]["revision"] == 2
    assert client.post(path, headers=admin, json=initial).status_code == 409
    give_grant(client, admin, base, prompt, policy, creds[1][0])
    resolve = base + "/prompts/resolve/" + prompt["key"]
    resolved = client.post(
        resolve, headers=creds[1][1], json=context(provider="local", alias="staging")
    )
    assert resolved.status_code == 200, resolved.text
    assert (
        resolved.json()["root_version_id"] == second["id"]
        and resolved.json()["alias"]["revision"] == 2
    )
    comparison = {**context(), "left_version_id": first["id"], "right_version_id": second["id"]}
    diff = client.post(base + "/prompts/" + prompt["id"] + "/diff", headers=admin, json=comparison)
    assert diff.status_code == 200 and "REVISED_PRIVATE_INSTRUCTION" in diff.json()["diff"]
    assert (
        client.post(
            base + "/prompts/" + prompt["id"] + "/diff", headers=tenant[2], json=comparison
        ).status_code
        == 403
    )
    rollback = {
        "receipt_id": changed["receipt"]["id"],
        "expected_revision": 2,
        "approval_ref": "synthetic-rollback",
    }
    alias_path = base + "/prompt-aliases/" + changed["alias"]["id"]
    rolled = client.post(alias_path + "/rollback", headers=admin, json=rollback)
    assert rolled.status_code == 201, rolled.text
    assert (
        rolled.json()["alias"]["version_id"] == first["id"]
        and rolled.json()["alias"]["revision"] == 3
    )
    assert (
        client.post(alias_path + "/rollback", headers=admin, json=rollback).json()["status"]
        == "duplicate"
    )
    history = client.get(alias_path + "/history", headers=tenant[2]).json()
    assert (
        len(history["items"]) == 3
        and history["items"][0]["rollback_of"] == changed["receipt"]["id"]
    )
    assert history["items"][2]["to_version_id"] == first["id"]
    assert "OWNED_PRIVATE_INSTRUCTION" not in str(history)


def test_alias_cannot_restore_expired_content_or_claim_an_unbound_experiment(
    api, tenant, prompt_setup
):
    from cutback.server.db import ExperimentRunRecord, PayloadSample

    client, admin, app = api
    base, policy, _ = prompt_setup
    prompt = create_prompt(client, admin, base)
    first = post_version(client, admin, base, prompt, version_body(policy)).json()
    second = post_version(
        client,
        admin,
        base,
        prompt,
        version_body(
            policy,
            {"text": "Version two {{input}}", "variables": {"input": "string"}},
            expected_latest_ordinal=1,
        ),
    ).json()
    path = base + "/prompts/" + prompt["id"] + "/aliases"
    initial = {
        "version_id": first["id"],
        "environment": "staging",
        "alias": "staging",
        "expected_revision": 0,
        "approval_ref": "synthetic-initial",
        "quality_unverified_acknowledged": True,
    }
    assert client.post(path, headers=admin, json=initial).status_code == 201
    plan = client.get(base + "/experiments/plans", headers=admin).json()[0]
    with app.state.sessions.begin() as session:
        unrelated = ExperimentRunRecord(
            project_id=UUID(tenant[0]),
            plan_id=UUID(plan["id"]),
            status="COMPLETED",
            results={"billing_complete": True},
        )
        session.add(unrelated)
        session.flush()
        unrelated_id = str(unrelated.id)
    changed = {
        **initial,
        "version_id": second["id"],
        "expected_revision": 1,
        "expected_version_id": first["id"],
        "approval_ref": "synthetic-change",
    }
    refusal = client.post(
        path,
        headers=admin,
        json={
            **changed,
            "quality_unverified_acknowledged": False,
            "evidence_run_id": unrelated_id,
            "evidence_model_key": "candidate",
        },
    )
    assert (
        refusal.status_code == 409
        and refusal.json()["code"] == "EXACT_PROMPT_TEST_BINDING_REQUIRED"
    )
    assert client.get(path, headers=admin).json()["items"][0]["revision"] == 1
    updated = client.post(path, headers=admin, json=changed).json()
    with app.state.sessions.begin() as session:
        version = session.get(PromptVersion, UUID(first["id"]))
        session.get(PayloadSample, version.payload_id).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    rollback = client.post(
        base + "/prompt-aliases/" + updated["alias"]["id"] + "/rollback",
        headers=admin,
        json={
            "receipt_id": updated["receipt"]["id"],
            "expected_revision": 2,
            "approval_ref": "synthetic-expired-rollback",
        },
    )
    assert rollback.status_code == 410
    current = client.get(path, headers=admin).json()["items"][0]
    assert current["version_id"] == second["id"] and current["revision"] == 2


def test_independent_example_uses_actual_scoped_api_and_local_receipt_validation(
    api, tenant, prompt_setup
):
    import runpy
    from pathlib import Path

    import httpx

    client, admin, app = api
    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/prompt-releases/sample.py")
    )

    def forward(request):
        response = client.request(
            request.method,
            request.url.raw_path.decode(),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    result = sample["demonstrate"](
        "https://cutback.example",
        tenant[0],
        admin["Authorization"][7:],
        transport=httpx.MockTransport(forward),
    )
    assert (
        result["versions"] == 2 and result["alias_revision"] == 3 and result["provider_calls"] == 0
    )
    assert result["quality"] == "unverified_synthetic_lifecycle_only"


def test_oidc_reader_rechecks_live_grant_and_delete_removes_prompt_objects(
    api, tenant, prompt_setup
):
    from test_identity_teams import identity

    from cutback.server.db import PayloadSample
    from cutback.server.identity import subject_digest
    from cutback.server.prompt_db import PromptRecord

    client, admin, app = api
    base, policy, creds = prompt_setup
    verifier, token = identity.__wrapped__()
    app.state.oidc_verifier = verifier
    prompt = create_prompt(client, creds[0][1], base)
    version = post_version(client, creds[0][1], base, prompt, version_body(policy)).json()
    organization = client.get(base, headers=admin).json()["organization_id"]
    member = client.post(
        f"/v1/organizations/{organization}/members",
        headers=admin,
        json={"issuer": verifier.issuer, "subject": "synthetic-prompt-user"},
    ).json()["id"]
    grant_path = f"/v1/organizations/{organization}/members/{member}/grants"
    assert (
        client.post(
            grant_path, headers=admin, json={"project_id": tenant[0], "role": "prompt_reader"}
        ).status_code
        == 200
    )
    actor = "oidc:" + subject_digest(verifier.issuer, "synthetic-prompt-user")[:32]
    give_grant(client, admin, base, prompt, policy, actor)
    path = base + "/prompts/resolve/" + prompt["key"]
    headers = {"Authorization": "Bearer " + token(role="admin", sub="synthetic-prompt-user")}
    resolve = context(provider="local", version_id=version["id"])
    assert client.post(path, headers=headers, json=resolve).status_code == 200
    assert client.delete(grant_path + "/" + tenant[0], headers=admin).status_code == 200
    assert client.post(path, headers=headers, json=resolve).status_code == 403
    exported = client.get(base + "/export", headers=admin)
    assert exported.status_code == 200 and "OWNED_PRIVATE_INSTRUCTION" not in exported.text
    slug = client.get(base, headers=admin).json()["slug"]
    deleted = client.delete(base, headers=admin, params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PromptRecord)
                .where(PromptRecord.project_id == UUID(tenant[0]))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(PayloadSample.project_id == UUID(tenant[0]))
            )
            == 0
        )
