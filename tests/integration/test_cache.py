import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.cache_api import expire_cache
from cutback.server.enterprise_db import CacheEntry

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    # This worker drill expires every eligible tenant, not only this test's
    # project. Give its exact one-entry assertion a fresh migrated database.
    return isolated_postgres_url


def test_cache_requires_explicit_rights_scope_and_immutable_context(
    api, tenant, monkeypatch, tmp_path
):
    client, admin, app = api
    project, ingest, viewer = tenant
    path = f"/v1/projects/{project}"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    policy = client.post(
        path + "/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "synthetic-cache",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": 1,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    ).json()["id"]
    key = {
        "project_id": project,
        "policy_id": policy,
        "key_sha256": "a" * 64,
        "workflow": "classify",
        "environment": "staging",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "prompt_revision": "v1",
        "freshness_ref": "catalog-v1",
    }
    body = {
        **key,
        "response": "PRIVATE synthetic response",
        "ttl_seconds": 60,
        "immutable_or_safely_invalidated": True,
    }
    assert client.post("/v1/ingest/cache/put", headers=ingest, json=body).status_code == 503
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    assert client.post("/v1/ingest/cache/put", headers=viewer, json=body).status_code == 403
    assert (
        client.post(
            "/v1/ingest/cache/put", headers=ingest, json={**body, "environment": "production"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/ingest/cache/put",
            headers=ingest,
            json={**body, "response": "private person@example.com"},
        ).status_code
        == 422
    )
    result = client.post("/v1/ingest/cache/put", headers=ingest, json=body)
    assert result.status_code == 201, result.text
    assert client.post("/v1/ingest/cache/put", headers=ingest, json=body).status_code == 409
    assert (
        client.post(
            "/v1/ingest/cache/put", headers=ingest, json={**body, "key_sha256": "b" * 64}
        ).status_code
        == 429
    )
    assert (
        client.post("/v1/ingest/cache/lookup", headers=ingest, json=key).json()["response"]
        == body["response"]
    )
    assert not client.post(
        "/v1/ingest/cache/lookup", headers=ingest, json={**key, "prompt_revision": "v2"}
    ).json()["hit"]
    assert (
        client.post(
            "/v1/ingest/cache/lookup", headers=ingest, json={**key, "project_id": str(uuid4())}
        ).status_code
        == 403
    )
    assert b"PRIVATE" not in client.get(path + "/export", headers=admin).content
    with app.state.sessions() as session:
        row = session.scalar(select(CacheEntry).where(CacheEntry.project_id == UUID(project)))
        ref = row.object_ref
        assert b"PRIVATE" not in store._path(project, ref).read_bytes()
    # The sample quota is shared with SDK capture and dataset imports.
    sample = {
        "sample_id": str(uuid4()),
        "event_id": str(uuid4()),
        "policy_id": policy,
        "environment": "staging",
        "workflow": "classify",
        "provider": "openai",
        "input": "synthetic",
    }
    assert client.post("/v1/ingest/samples", headers=ingest, json=sample).status_code == 429
    client.delete(path + "/capture-policies/" + policy, headers=admin)
    assert client.post("/v1/ingest/cache/lookup", headers=ingest, json=key).status_code == 403
    assert (
        expire_cache(app.state.sessions, store, now=datetime.now(UTC) + timedelta(minutes=2))[
            "cache_entries_expired"
        ]
        == 1
    )
    assert not store._path(project, ref).exists()
