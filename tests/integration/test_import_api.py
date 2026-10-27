import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import CapturePolicyRecord, DatasetRecord, PayloadSample
from cutback.server.worker import load_dataset

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


def test_preview_creator_scope_resume_revocation_and_commit_storage_compensation(
    api, tenant, import_body, monkeypatch
):
    client, admin, app = api
    project = tenant[0]
    base = f"/v1/projects/{project}"
    credentials = []
    for _ in range(2):
        key = client.post(base + "/keys", headers=admin, json={"role": "analyst"}).json()
        credentials.append({"Authorization": "Bearer " + key["token"]})
    response = client.post(base + "/imports/preview", headers=credentials[0], json=import_body)
    assert response.status_code == 201, response.text
    preview = response.json()
    path = base + "/imports/" + preview["id"]
    assert (
        client.post(
            path + "/content", headers=credentials[1], json={"purpose": "other-reviewer"}
        ).status_code
        == 403
    )
    resumed = client.get(path, headers=credentials[0])
    assert resumed.status_code == 200 and resumed.json()["preview_hash"] == preview["preview_hash"]
    assert "Synthetic urgent ticket" not in resumed.text
    commit = {
        "preview_hash": preview["preview_hash"],
        "mapping_reviewed": True,
        "rights_confirmed": True,
    }
    assert client.post(path + "/commit", headers=credentials[1], json=commit).status_code == 403
    files = {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()}
    original_put, calls = app.state.artifact_store.put, 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private-import-storage-diagnostic")
        return original_put(*args)

    monkeypatch.setattr(app.state.artifact_store, "put", fail_second)
    failed = client.post(path + "/commit", headers=credentials[0], json=commit)
    assert failed.status_code == 500 and "private-import-storage-diagnostic" not in failed.text
    assert {p for p in app.state.artifact_store.root.rglob("*") if p.is_file()} == files
    assert client.get(path, headers=credentials[0]).json()["state"] == "pending"
    assert client.get(base + "/datasets", headers=admin).json() == []
    monkeypatch.setattr(app.state.artifact_store, "put", original_put)
    with app.state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(import_body["policy_id"])).enabled = False
    assert client.post(path + "/commit", headers=credentials[0], json=commit).status_code == 403
    assert (
        client.post(
            path + "/content", headers=credentials[0], json={"purpose": "revoked"}
        ).status_code
        == 403
    )
    with app.state.sessions.begin() as session:
        session.get(CapturePolicyRecord, UUID(import_body["policy_id"])).enabled = True
    assert client.post(path + "/commit", headers=credentials[0], json=commit).status_code == 201


@pytest.fixture
def import_body(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    project, _, _ = tenant
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    policy = client.post(
        f"/v1/projects/{project}/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "synthetic-import",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["local"],
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "sample_rate": 1,
            "max_samples": 20,
            "max_payload_bytes": 64000,
        },
    ).json()["id"]
    text = (ROOT / "tests/fixtures/imports/promptfoo-0.125.1-v3.json").read_text(encoding="utf-8")
    return {
        "profile": "promptfoo-0.125.1-results.v3",
        "source_text": text,
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "policy_id": policy,
        "environment": "staging",
        "workflow": "classify",
        "provenance_ref": "owned-synthetic-producer",
    }


def test_encrypted_reviewed_import_is_atomic_idempotent_and_does_not_verify_source_grades(
    api, tenant, import_body
):
    client, admin, app = api
    project, writer, reader = tenant
    base = f"/v1/projects/{project}/imports"
    for headers in (writer, reader):
        assert client.post(base + "/preview", headers=headers, json=import_body).status_code == 403
    preview = client.post(base + "/preview", headers=admin, json=import_body)
    assert preview.status_code == 201, preview.text
    summary = preview.json()
    assert (
        summary["accepted"] == 2
        and summary["content_uploaded"] is True
        and summary["dataset_created"] is False
    )
    assert "Synthetic urgent ticket" not in preview.text
    items = client.get(base, headers=reader).json()["items"]
    assert items[0]["state"] == "pending" and "Synthetic urgent ticket" not in json.dumps(items)
    content_path = base + "/" + summary["id"] + "/content"
    assert client.post(content_path, headers=reader, json={"purpose": "review"}).status_code == 403
    content = client.post(content_path, headers=admin, json={"purpose": "synthetic-import-review"})
    assert content.status_code == 200 and content.json()["cases"][0]["reference"] == "urgent"
    assert not content.json()["independent_labels"]
    commit = {
        "preview_hash": summary["preview_hash"],
        "mapping_reviewed": True,
        "rights_confirmed": True,
    }
    path = base + "/" + summary["id"] + "/commit"
    assert (
        client.post(path, headers=admin, json={**commit, "preview_hash": "0" * 64}).status_code
        == 409
    )
    assert (
        client.post(path, headers=admin, json={**commit, "mapping_reviewed": False}).status_code
        == 422
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(path, headers=admin, json=commit), range(2)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert results[0].json()["id"] == results[1].json()["id"]
    dataset_id = UUID(results[0].json()["id"])
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetRecord)
                .where(DatasetRecord.project_id == UUID(project))
            )
            == 1
        )
        dataset = session.get(DatasetRecord, dataset_id)
        assert dataset.provenance["source_profile"] == import_body["profile"]
        assert dataset.provenance["source_asserted_checks"][0]["evidence"] == "externally_asserted"
        loaded, _ = load_dataset(session, dataset, app.state.artifact_store)
        assert loaded["items"][0]["case"]["gold"] == "urgent"
        assert (
            not dataset.provenance["independent_labels"]
            and not dataset.provenance["representative"]
        )
    assert not any(
        b"Synthetic urgent ticket" in p.read_bytes()
        for p in app.state.artifact_store.root.rglob("*")
        if p.is_file()
    )
    assert (
        "Synthetic urgent ticket"
        not in client.get(f"/v1/projects/{project}/export", headers=admin).text
    )


def test_import_scope_expiry_rejections_and_quota_are_enforced_before_dataset_creation(
    api, tenant, import_body
):
    client, admin, app = api
    project, _, _ = tenant
    base = f"/v1/projects/{project}/imports"
    changed = client.post(
        base + "/preview", headers=admin, json={**import_body, "source_sha256": "0" * 64}
    )
    assert changed.status_code == 409
    native = '{"id":"one","input":"Synthetic input"}\n{"id":"bad","input":123}\n'
    body = {
        **import_body,
        "profile": "cutback-jsonl.v1",
        "source_text": native,
        "source_sha256": hashlib.sha256(native.encode()).hexdigest(),
    }
    response = client.post(base + "/preview", headers=admin, json=body)
    assert response.status_code == 201
    preview = response.json()
    assert preview["accepted"] == preview["rejected"] == 1
    path = base + "/" + preview["id"] + "/commit"
    commit = {
        "preview_hash": preview["preview_hash"],
        "mapping_reviewed": True,
        "rights_confirmed": True,
    }
    assert client.post(path, headers=admin, json=commit).status_code == 409
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-import-other-" + uuid4().hex}
    ).json()["id"]
    assert client.post(path.replace(project, other), headers=admin, json=commit).status_code == 404
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(body["policy_id"]))
        policy.policy = {**policy.policy, "max_samples": 1}
    assert (
        client.post(path, headers=admin, json={**commit, "accept_rejections": True}).status_code
        == 429
    )
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(body["policy_id"]))
        policy.policy = {**policy.policy, "max_samples": 20}
        session.execute(
            update(PayloadSample)
            .where(PayloadSample.id == UUID(preview["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert (
        client.post(path, headers=admin, json={**commit, "accept_rejections": True}).status_code
        == 410
    )
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DatasetRecord)
                .where(DatasetRecord.project_id == UUID(project))
            )
            == 0
        )
