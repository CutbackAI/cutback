"""Profile sidecars are explicitly authorized, encrypted and share sample quotas."""

import base64
import importlib
import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from uuid import UUID, uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from openai import OpenAI
from support_http import owned_collector
from unit.test_native_cache import native_body

import cutback
from cutback.core.native_request_profile import NativeRequestProfileSnapshot
from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.datasets import CapturePolicy, Case, digest
from cutback.instrumentation import workflow
from cutback.server.db import PayloadSample, StoredEvent
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import recover_native_profiles, run_native_profile_one

pytestmark = pytest.mark.postgres


def queue_analysis(api, tenant, sample, **changes):
    now = datetime.now(UTC)
    body = {
        "request_id": str(uuid4()),
        "policy_id": sample["policy_id"],
        "environment": "staging",
        "workflow": "classify",
        "purpose": "owned-cache-assessment",
        "window_start": (now - timedelta(minutes=5)).isoformat(),
        "window_end": now.isoformat(),
        **changes,
    }
    response = api[0].post(
        f"/v1/projects/{tenant[0]}/native-request-profiles/analyses",
        headers=api[1],
        json=body,
    )
    return response, body


def retained_profile_stream(api, tenant, tmp_path, monkeypatch, event, *, missing=False):
    sample, store, snapshot = profile_capture(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        max_samples=10,
    )
    now = datetime.now(UTC) - timedelta(seconds=20)
    records = []
    for index in range(3 if missing else 2):
        native = event.model_copy(
            update={
                "event_id": uuid4(),
                "occurred_at": now + timedelta(seconds=index * 2),
                "workflow_id": "classify",
                "duration_ms": 100,
            }
        )
        records.append(native.model_dump(mode="json"))
    response = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={
            "project_id": tenant[0],
            "events": records,
        },
    )
    assert response.status_code == 200
    for index, item in enumerate(records[:2]):
        body = {
            **sample,
            "sample_id": str(uuid4()),
            "event_id": item["event_id"],
            "native_request_profile": {
                **sample["native_request_profile"],
                "monotonic_started_ns": index * 2000000000,
            },
        }
        assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=body).status_code == 201
    return sample, store, snapshot


@pytest.mark.parametrize("missing", [False, True])
def test_analysis_uses_canonical_metadata_denominator_and_protected_profiles(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    missing,
):
    sample, store, snapshot = retained_profile_stream(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        event,
        missing=missing,
    )
    response, body = queue_analysis(api, tenant, sample)
    assert response.status_code == 201
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "COMPLETED"
    )
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/{body['request_id']}"
    result = api[0].get(path, headers=api[1])
    summary = result.json()["summary"]
    assert summary["profiled_attempts"] == 2
    assert summary["metadata_attempts"] == (3 if missing else 2)
    assert summary["modeled_potential_hits"] == 1
    assert summary["cache_trial_eligible"] is not missing
    assert summary["realized_savings_usd"] is None
    assert snapshot.request_hmac_sha256 not in result.text
    if missing:
        assert "MISSING_PROFILED_ATTEMPTS" in summary["abstention_reasons"]


@pytest.mark.parametrize(
    "failure", ["revoked_policy", "missing_blob", "storage_outage", "hash_conflict"]
)
def test_current_authority_and_failed_storage_never_replay_or_leak_content(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
    failure,
):
    from cutback.server.db import CapturePolicyRecord

    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    response, body = queue_analysis(api, tenant, sample)
    assert response.status_code == 201
    reads, original = [], store.get

    def get(*args):
        reads.append(1)
        if failure == "storage_outage":
            raise OSError("PRIVATE_OWNED_STORAGE_FAILURE")
        return original(*args)

    monkeypatch.setattr(store, "get", get)
    with api[2].state.sessions.begin() as session:
        if failure == "revoked_policy":
            session.get(CapturePolicyRecord, UUID(sample["policy_id"])).enabled = False
        elif failure in {"missing_blob", "hash_conflict"}:
            for row in session.query(PayloadSample).filter_by(project_id=UUID(tenant[0])):
                if failure == "missing_blob":
                    store.delete(UUID(tenant[0]), row.native_profile_ref)
                else:
                    row.native_profile_sha256 = "0" * 64
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "FAILED"
    )
    assert len(reads) == (0 if failure == "revoked_policy" else 1)
    before = len(reads)
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "IDLE"
    )
    assert len(reads) == before
    result = api[0].get(
        f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/{body['request_id']}",
        headers=api[1],
    )
    assert result.json()["summary"] is None and "PRIVATE_OWNED" not in result.text


def test_profile_analysis_revoked_actor_cannot_read_and_other_tenant_cannot_find_receipt(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    event,
):
    from cutback.server.db import AccessKey

    sample, store, _ = retained_profile_stream(api, tenant, tmp_path, monkeypatch, event)
    key = (
        api[0]
        .post(
            f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin"},
        )
        .json()
    )
    _, body = queue_analysis(api, tenant, sample)
    body["request_id"] = str(uuid4())
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses"
    assert (
        api[0]
        .post(path, headers={"Authorization": "Bearer " + key["token"]}, json=body)
        .status_code
        == 201
    )
    with api[2].state.sessions.begin() as session:
        session.get(AccessKey, UUID(key["key_id"])).revoked_at = datetime.now(UTC)
    reads = []
    monkeypatch.setattr(store, "get", lambda *args: reads.append(1))
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "FAILED"
    )
    assert not reads
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-other-" + uuid4().hex})
        .json()["id"]
    )
    assert (
        api[0]
        .get(
            f"/v1/projects/{other}/native-request-profiles/analyses/{body['request_id']}",
            headers=api[1],
        )
        .status_code
        == 404
    )


def test_profile_sidecars_follow_payload_expiry_and_project_deletion(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    from cutback.server.db import Project
    from cutback.server.worker import expire_payloads

    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    with api[2].state.sessions() as session:
        row = session.get(PayloadSample, UUID(sample["sample_id"]))
        refs = row.object_ref, row.native_profile_ref
        expires = row.expires_at
    expire_payloads(api[2].state.sessions, store, now=expires + timedelta(seconds=1))
    for ref in refs:
        with pytest.raises((ValueError, FileNotFoundError)):
            store.get(UUID(tenant[0]), ref)
    queued, _ = queue_analysis(api, tenant, sample)
    assert queued.status_code == 201
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    deleted = api[0].delete(
        f"/v1/projects/{tenant[0]}?confirm_slug={slug}",
        headers=api[1],
    )
    assert deleted.status_code == 200
    with api[2].state.sessions() as session:
        assert not session.query(NativeProfileAnalysis).filter_by(project_id=UUID(tenant[0])).all()


def test_analysis_requires_admin_explicit_rights_and_exact_idempotent_request(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    response, body = queue_analysis(api, tenant, sample)
    assert response.status_code == 201
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses"
    assert api[0].post(path, headers=api[1], json=body).json() == response.json()
    assert api[0].post(path, headers=tenant[2], json=body).status_code == 403
    assert api[0].post(path, headers=tenant[1], json=body).status_code == 403
    assert api[0].post(path, headers=api[1], json={**body, "purpose": "changed"}).status_code == 409
    assert api[0].get(path, headers=api[1]).json()[0]["id"] == body["request_id"]
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "COMPLETED"
    )
    result = api[0].get(path + "/" + body["request_id"], headers=api[1]).json()
    assert result["summary"]["cache_trial_eligible"] is False
    assert "NO_RETAINED_PROFILES" in result["summary"]["abstention_reasons"]
    assert result["attempts"] == 1 and result["provider_calls"] == 0
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "IDLE"
    )


def test_cancel_and_uncertain_storage_lease_are_terminal_without_replay(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    response, body = queue_analysis(api, tenant, sample)
    assert response.status_code == 201
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/{body['request_id']}"
    assert api[0].post(path + "/cancel", headers=api[1], json={}).json()["state"] == "CANCELLED"
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "IDLE"
    )
    response, body = queue_analysis(api, tenant, sample)
    with api[2].state.sessions.begin() as session:
        row = session.get(NativeProfileAnalysis, UUID(body["request_id"]))
        row.state, row.attempts = "RUNNING", 1
        row.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    recover_native_profiles(api[2].state.sessions)
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/{body['request_id']}"
    result = api[0].get(path, headers=api[1]).json()
    assert result["state"] == "FAILED" and "NO_REPLAY" in result["failure_code"]
    assert (
        run_native_profile_one(api[2].state.sessions, store, only_analysis_id=body["request_id"])[
            "status"
        ]
        == "IDLE"
    )


def test_expired_summary_is_pruned_but_one_shot_identity_cannot_dispatch_again(
    api, tenant, tmp_path, monkeypatch
):
    sample, _, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    response, body = queue_analysis(api, tenant, sample)
    assert response.status_code == 201
    with api[2].state.sessions.begin() as session:
        row = session.get(NativeProfileAnalysis, UUID(body["request_id"]))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        row.summary = {"must_disappear": True}
    path = f"/v1/projects/{tenant[0]}/native-request-profiles/analyses"
    assert api[0].get(path + "/" + body["request_id"], headers=api[1]).json()["summary"] is None
    assert api[0].get(path, headers=api[1]).json() == []
    recover_native_profiles(api[2].state.sessions)
    assert api[0].get(path + "/" + body["request_id"], headers=api[1]).json()["state"] == "EXPIRED"
    assert api[0].post(path, headers=api[1], json=body).status_code == 409
    assert recover_native_profiles(api[2].state.sessions)["recovered_or_expired"] == 0


def profile_capture(api, tenant, tmp_path, monkeypatch, *, approved=True, max_samples=2, denied=()):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    body = {
        "enabled": True,
        "approval_ref": "owned-profile-capture",
        "rights_basis": "synthetic",
        "environments": ["staging"],
        "workflows": ["classify"],
        "allowed_providers": ["openai"],
        "sample_rate": 1,
        "max_samples": max_samples,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "never_capture_fields": list(denied),
    }
    if approved:
        body["native_request_profile"] = {
            "freshness_ref": "owned-immutable-v1",
            "prospective_ttl_seconds": 300,
            "fingerprint_capture_authorized": True,
            "immutable_or_safely_invalidated": True,
        }
    policy = CapturePolicy.model_validate(body)
    response = api[0].post(f"/v1/projects/{tenant[0]}/capture-policies", headers=api[1], json=body)
    assert response.status_code == 201
    snapshot = NativeRequestProfileSnapshot(
        capture_policy_sha256=digest(policy.model_dump(mode="json")),
        profile_key_id=uuid4(),
        clock_domain_id=uuid4(),
        clock_basis="process_perf_counter_ns_v1",
        monotonic_started_ns=0,
        request_hmac_sha256="d" * 64,
        sdk_version=version("openai"),
        response_cache_eligible=True,
    )
    sample = {
        "sample_id": str(uuid4()),
        "event_id": str(uuid4()),
        "policy_id": response.json()["id"],
        "environment": "staging",
        "workflow": "classify",
        "provider": "openai",
        "input": "owned synthetic input",
        "reference": "owned synthetic answer",
        "native_request_profile": snapshot.model_dump(mode="json"),
    }
    return sample, store, snapshot


def test_profile_is_a_separate_encrypted_object_and_not_in_metadata_export(
    api, tenant, tmp_path, monkeypatch
):
    sample, store, snapshot = profile_capture(api, tenant, tmp_path, monkeypatch)
    response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert response.status_code == 201
    with api[2].state.sessions() as session:
        row = session.get(PayloadSample, UUID(sample["sample_id"]))
        assert row.native_profile_ref and row.native_profile_ref != row.object_ref
        assert row.native_profile_sha256 == digest(snapshot.model_dump(mode="json"))
        case = json.loads(store.get(UUID(tenant[0]), row.object_ref))
        assert Case.model_validate(case).input == "owned synthetic input"
        assert "native_request_profile" not in case
        protected = store.get(UUID(tenant[0]), row.native_profile_ref)
        assert NativeRequestProfileSnapshot.model_validate_json(protected) == snapshot
        assert "d" * 64 not in str(row.metadata_safe)
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 2
    assert not any(
        b"d" * 64 in path.read_bytes() for path in store.root.rglob("*") if path.is_file()
    )
    exported = api[0].get(f"/v1/projects/{tenant[0]}/export", headers=api[1])
    assert exported.status_code == 200
    assert snapshot.request_hmac_sha256 not in exported.text
    assert str(snapshot.profile_key_id) not in exported.text
    assert row.native_profile_ref not in exported.text


@pytest.mark.parametrize(
    "approved,denied", [(False, ()), (True, ("input",)), (True, ("reference",))]
)
def test_profile_capture_cannot_bypass_missing_rights_or_field_exclusions(
    api, tenant, tmp_path, monkeypatch, approved, denied
):
    sample, store, _ = profile_capture(
        api, tenant, tmp_path, monkeypatch, approved=approved, denied=denied
    )
    response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert response.status_code == 403
    assert not any(path.is_file() for path in store.root.rglob("*"))


def test_profile_pair_uses_two_approved_object_slots_and_exact_retries_use_none(
    api, tenant, tmp_path, monkeypatch
):
    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch, max_samples=2)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    replay = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
    assert replay.status_code == 201 and replay.json()["status"] == "duplicate"
    next_sample = {**sample, "sample_id": str(uuid4()), "event_id": str(uuid4())}
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=next_sample).status_code == 429
    changed = {
        **sample,
        "native_request_profile": {
            **sample["native_request_profile"],
            "request_hmac_sha256": "e" * 64,
        },
    }
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=changed).status_code == 409
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 2


def test_lost_pair_commit_ack_keeps_both_committed_objects(api, tenant, tmp_path, monkeypatch):
    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    begin, lost = sessions.begin, []

    @contextmanager
    def uncertain():
        with begin() as session:
            yield session
        with sessions() as session:
            present = session.get(PayloadSample, UUID(sample["sample_id"])) is not None
        if present and not lost:
            lost.append(True)
            raise ConnectionError("OWNED_PROFILE_ACK_LOST")

    with monkeypatch.context() as fault:
        fault.setattr(sessions, "begin", uncertain)
        response = api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample)
        assert response.status_code == 500
    assert lost
    assert (
        api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).json()["status"]
        == "duplicate"
    )
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 2


def test_populated_profile_downgrade_refuses_erasing_the_sidecar_pointer(
    api, tenant, tmp_path, monkeypatch
):
    sample, _, _ = profile_capture(api, tenant, tmp_path, monkeypatch)
    assert api[0].post("/v1/ingest/samples", headers=tenant[1], json=sample).status_code == 201
    migration = importlib.import_module(
        "cutback.server.migrations.versions.0032_native_request_profiles"
    )
    with api[2].state.sessions.begin() as session:
        with Operations.context(MigrationContext.configure(session.connection())):
            with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_NATIVE_PROFILES"):
                migration.downgrade()


@pytest.mark.parametrize("key_configured", [False, True])
def test_actual_sdk_profiles_stay_in_encrypted_capture_and_never_cache_or_change_calls(
    api, tenant, tmp_path, monkeypatch, key_configured
):
    sample, store, _ = profile_capture(api, tenant, tmp_path, monkeypatch, max_samples=8)
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", tenant[1]["Authorization"][7:])
    if key_configured:
        monkeypatch.setenv(
            "CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY",
            base64.b64encode(b"owned-synthetic-profile-key-material" * 2).decode(),
        )
    else:
        monkeypatch.delenv("CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY", raising=False)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=native_body())

    with owned_collector(api[2]) as endpoint:
        observer = cutback.init(
            service_name="owned-profile-fixture",
            environment="staging",
            enabled=True,
            endpoint=endpoint + "/v1/ingest/events",
            project_id=tenant[0],
            capture_content=True,
            capture_policy_id=sample["policy_id"],
            timeout_seconds=2,
        )
        try:
            with OpenAI(
                api_key="owned-native-profile-fixture",
                max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(provider), trust_env=False),
            ) as client:
                with workflow("classify"):
                    first = client.responses.create(
                        model="gpt-4.1",
                        input="owned synthetic repeat",
                        max_output_tokens=64,
                        store=False,
                    )
                    second = client.responses.create(
                        model="gpt-4.1",
                        input="owned synthetic repeat",
                        max_output_tokens=64,
                        store=False,
                    )
            assert first is not second and type(first) is type(second)
            assert first.output_text == second.output_text and len(calls) == 2
            assert observer.flush(5), observer.health()
            with api[2].state.sessions() as session:
                rows = (
                    session.query(PayloadSample)
                    .filter(PayloadSample.project_id == UUID(tenant[0]))
                    .all()
                )
                assert len(rows) == 2
                profiles = [
                    NativeRequestProfileSnapshot.model_validate_json(
                        store.get(UUID(tenant[0]), row.native_profile_ref)
                    )
                    for row in rows
                    if row.native_profile_ref
                ]
                assert len(profiles) == (2 if key_configured else 0)
                if key_configured:
                    assert profiles[0].request_hmac_sha256 == profiles[1].request_hmac_sha256
                    assert profiles[0].profile_key_id == profiles[1].profile_key_id
                    assert all(profile.response_cache_eligible for profile in profiles)
                events = (
                    session.query(StoredEvent)
                    .filter(StoredEvent.project_id == UUID(tenant[0]))
                    .all()
                )
                assert len(events) == 2
                metadata = json.dumps([event.event for event in events])
                assert "owned synthetic repeat" not in metadata
                assert "request_hmac_sha256" not in metadata
                assert "owned-native-profile-fixture" not in metadata
            if key_configured:
                assert observer.health()["native_profile_exported"] == 2
            else:
                assert observer.health()["native_profile_unconfigured"] == 1
            queued, analysis_body = queue_analysis(api, tenant, sample)
            assert queued.status_code == 201
            assert (
                run_native_profile_one(
                    api[2].state.sessions, store, only_analysis_id=analysis_body["request_id"]
                )["status"]
                == "COMPLETED"
            )
            analyzed = (
                api[0]
                .get(
                    f"/v1/projects/{tenant[0]}/native-request-profiles/analyses/"
                    + analysis_body["request_id"],
                    headers=api[1],
                )
                .json()["summary"]
            )
            assert analyzed["metadata_attempts"] == 2
            assert analyzed["profiled_attempts"] == (2 if key_configured else 0)
            assert analyzed["modeled_potential_hits"] == (1 if key_configured else 0)
            assert analyzed["cache_trial_eligible"] is key_configured
            assert analyzed["realized_savings_usd"] is None
        finally:
            observer.close()
