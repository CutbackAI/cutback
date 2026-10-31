"""SDK content ingestion preserves committed ciphertext after a lost acknowledgement."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import PayloadSample

pytestmark = pytest.mark.postgres


def prepared_capture(api, tenant, tmp_path, monkeypatch, *, max_samples=2):
    client, admin, app = api
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path / "owned-capture", os.urandom(32))
    app.state.artifact_store = store
    response = client.post(
        f"/v1/projects/{tenant[0]}/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "owned-capture-recovery",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["openai"],
            "sample_rate": 1,
            "max_samples": max_samples,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 201
    sample = {
        "sample_id": str(uuid4()),
        "event_id": str(uuid4()),
        "policy_id": response.json()["id"],
        "environment": "staging",
        "workflow": "classify",
        "provider": "openai",
        "input": "owned synthetic private input",
        "reference": "owned synthetic output",
    }
    return sample, store


def post_uncertain(client, ingest, body):
    try:
        response = client.post("/v1/ingest/samples", headers=ingest, json=body)
    except ConnectionError:
        return
    assert response.status_code >= 500
    assert "PRIVATE_SYNTHETIC_COMMIT_ACK" not in response.text


def test_lost_sample_commit_ack_keeps_ciphertext_and_exact_retry_does_not_rewrite(
    api, tenant, tmp_path, monkeypatch
):
    sample, store = prepared_capture(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    original_begin, lost = sessions.begin, []

    @contextmanager
    def uncertain():
        with original_begin() as session:
            yield session
        with sessions() as reader:
            committed = reader.get(PayloadSample, UUID(sample["sample_id"]))
        if committed is not None and not lost:
            lost.append(True)
            raise ConnectionError("PRIVATE_SYNTHETIC_COMMIT_ACK")

    with monkeypatch.context() as fault:
        fault.setattr(sessions, "begin", uncertain)
        post_uncertain(api[0], tenant[1], sample)
    assert lost
    with sessions() as reader:
        row = reader.get(PayloadSample, UUID(sample["sample_id"]))
        assert row is not None
        original_ref = row.object_ref
        original_content = store.get(UUID(tenant[0]), original_ref)
    retry = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert retry.status_code == 201 and retry.json()["status"] == "duplicate"
    with sessions() as reader:
        assert reader.get(PayloadSample, UUID(sample["sample_id"])).object_ref == original_ref
    assert store.get(UUID(tenant[0]), original_ref) == original_content


@pytest.mark.parametrize("field", ["input", "reference", "event_id", "workflow"])
def test_sample_id_cannot_acknowledge_different_content_or_event_binding(
    api, tenant, tmp_path, monkeypatch, field
):
    sample, _ = prepared_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    changed = {**sample, field: str(uuid4()) if field == "event_id" else "other"}
    response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=changed)
    assert response.status_code in {403, 409}


def test_sample_expiry_never_extends_past_current_authorization(api, tenant, tmp_path, monkeypatch):
    sample, _ = prepared_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    from cutback.server.db import CapturePolicyRecord

    with api[2].state.sessions() as session:
        row = session.get(PayloadSample, UUID(sample["sample_id"]))
        policy = session.get(CapturePolicyRecord, UUID(sample["policy_id"]))
        assert row.expires_at <= datetime.fromisoformat(policy.policy["expires_at"])


def test_sample_rollback_removes_only_the_unreferenced_owned_ciphertext(
    api, tenant, tmp_path, monkeypatch
):
    sample, store = prepared_capture(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    original_begin, interrupted = sessions.begin, []

    @contextmanager
    def fail_before_commit():
        with original_begin() as session:
            yield session
            session.flush()
            if (
                session.get(PayloadSample, UUID(sample["sample_id"])) is not None
                and not interrupted
            ):
                interrupted.append(True)
                raise ConnectionError("PRIVATE_SYNTHETIC_COMMIT_ACK")

    with monkeypatch.context() as fault:
        fault.setattr(sessions, "begin", fail_before_commit)
        post_uncertain(api[0], tenant[1], sample)
    assert interrupted
    with sessions() as session:
        assert session.get(PayloadSample, UUID(sample["sample_id"])) is None
    assert not any(path.is_file() for path in store.root.rglob("*"))


def test_missing_committed_sample_is_not_acknowledged_as_a_complete_duplicate(
    api, tenant, tmp_path, monkeypatch
):
    sample, store = prepared_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    with api[2].state.sessions() as session:
        ref = session.get(PayloadSample, UUID(sample["sample_id"])).object_ref
    store.delete(UUID(tenant[0]), ref)
    response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert response.status_code == 409
    assert response.json()["code"] == "SAMPLE_COMMITTED_CONTENT_REQUIRES_RECONCILIATION"
    assert not store._path(tenant[0], ref).exists()


def test_expired_sample_retry_does_not_renew_the_capture_or_recreate_ciphertext(
    api, tenant, tmp_path, monkeypatch
):
    sample, store = prepared_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    with api[2].state.sessions.begin() as session:
        row = session.get(PayloadSample, UUID(sample["sample_id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        ref = row.object_ref
    original = store._path(tenant[0], ref).read_bytes()
    response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert response.status_code == 409
    assert store._path(tenant[0], ref).read_bytes() == original


def test_concurrent_samples_cannot_overrun_the_same_approved_object_slot(
    api, tenant, tmp_path, monkeypatch
):
    sample, store = prepared_capture(api, tenant, tmp_path, monkeypatch, max_samples=1)
    other = {**sample, "sample_id": str(uuid4()), "event_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(api[0].post, "/v1/ingest/samples", headers=tenant[1], json=body)
            for body in (sample, other)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [201, 429]
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 1
