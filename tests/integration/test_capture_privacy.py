import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import PayloadSample, StoredEvent

pytestmark = pytest.mark.postgres


def test_policy_gates_redacted_encrypted_runtime_sample_and_project_delete(
    api, tenant, monkeypatch, tmp_path
):
    client, admin, app = api
    project, ingest, viewer = tenant
    path = f"/v1/projects/{project}"
    policy = {
        "enabled": True,
        "approval_ref": "synthetic-only",
        "rights_basis": "synthetic",
        "environments": ["staging"],
        "workflows": ["classify"],
        "allowed_providers": ["openai"],
        "sample_rate": 1,
        "max_samples": 2,
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    assert client.post(path + "/capture-policies", headers=admin, json=policy).status_code == 403
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    response = client.post(path + "/capture-policies", headers=admin, json=policy)
    assert response.status_code == 201, response.text
    policy_id = response.json()["id"]
    sample = {
        "sample_id": str(uuid4()),
        "event_id": str(uuid4()),
        "policy_id": policy_id,
        "environment": "staging",
        "workflow": "classify",
        "provider": "openai",
        "input": "private a@example.org",
        "reference": "private output",
    }
    assert client.post("/v1/ingest/samples", headers=viewer, json=sample).status_code == 403
    result = client.post("/v1/ingest/samples", headers=ingest, json=sample)
    assert result.status_code == 201, result.text
    assert (
        client.post("/v1/ingest/samples", headers=ingest, json=sample).json()["status"]
        == "duplicate"
    )
    with app.state.sessions() as session:
        stored = session.get(PayloadSample, UUID(sample["sample_id"]))
        plain = store.get(UUID(project), stored.object_ref)
        assert b"a@example.org" not in plain and b"[EMAIL]" in plain
        assert "private" not in str(stored.metadata_safe)
        assert not session.scalar(
            select(StoredEvent).where(StoredEvent.project_id == UUID(project))
        )
    exported = client.get(path + "/export", headers=admin)
    assert exported.status_code == 200 and b"private output" not in exported.content
    assert client.delete(path, headers=admin, params={"confirm_slug": "wrong"}).status_code == 409
    detail = client.get(path, headers=admin)
    assert detail.status_code == 200, detail.text
    slug = detail.json()["slug"]
    deleted = client.delete(path, headers=admin, params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    assert not list((tmp_path / project).iterdir())
    assert client.get(path + "/usage", headers=viewer).status_code == 401
