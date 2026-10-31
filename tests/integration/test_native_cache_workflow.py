"""Native Responses cache through actual PostgreSQL rights and encrypted storage."""

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import func, select
from support_agent import native_response
from support_evaluation import authorized_dataset

from cutback.native_cache import NativeCacheScope, NativeResponsesCache
from cutback.server.db import CapturePolicyRecord
from cutback.server.enterprise_db import CacheEntry

pytestmark = pytest.mark.postgres


def prepared(api, tenant, tmp_path, monkeypatch):
    _, _, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    scope = NativeCacheScope(
        endpoint="http://127.0.0.1",
        project_id=tenant[0],
        policy_id=dataset["provenance"]["policy_id"],
        token=tenant[1]["Authorization"][7:],
        request_hmac_key=b"owned-synthetic-key-material-" + b"x" * 32,
        workflow="classify",
        environment="staging",
        prompt_revision="owned-prompt-v1",
        freshness_ref="owned-fixture-v1",
        ttl_seconds=60,
        content_retention_authorized=True,
        immutable_or_safely_invalidated=True,
    )
    calls = []

    def handle(request):
        calls.append(request)
        response = native_response(request)
        body = json.loads(response.content)
        body.update(parallel_tool_calls=False, tool_choice="none", tools=[])
        body["output"][0]["content"][0]["text"] = "PRIVATE_SYNTHETIC_CACHE_OUTPUT"
        return httpx.Response(200, json=body)

    client = OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False),
    )
    cache = NativeResponsesCache(scope, http_client=api[0])
    return client, cache, calls


def request(text="PRIVATE_SYNTHETIC_CACHE_INPUT"):
    return {
        "model": "gpt-4.1",
        "input": text,
        "instructions": "Owned synthetic task",
        "max_output_tokens": 64,
        "store": False,
    }


def test_native_hit_uses_encrypted_server_state_and_current_policy(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        first = cache.create(client, **request())
        second = cache.create(client, **request())
        assert first.receipt.cache_write == "created"
        assert second.receipt.origin == "encrypted_cache" and len(calls) == 1
        assert (
            second.response.output_text
            == first.response.output_text
            == "PRIVATE_SYNTHETIC_CACHE_OUTPUT"
        )
        with api[2].state.sessions() as session:
            count = session.scalar(
                select(func.count())
                .select_from(CacheEntry)
                .where(CacheEntry.project_id == UUID(tenant[0]))
            )
        assert count == 1
        assert not any(
            b"PRIVATE_SYNTHETIC_CACHE_OUTPUT" in p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file()
        )
        with api[2].state.sessions.begin() as session:
            policy = session.get(CapturePolicyRecord, cache.scope.policy_id)
            policy.enabled = False
        with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
            cache.create(client, **request())
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("change", ["viewer", "environment", "foreign_project"])
def test_scope_denial_happens_before_provider_dispatch(api, tenant, tmp_path, monkeypatch, change):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        if change == "viewer":
            update = {"token": tenant[2]["Authorization"][7:]}
        elif change == "environment":
            update = {"environment": "production"}
        else:
            other = api[0].post(
                "/v1/projects", headers=api[1], json={"slug": "owned-cache-foreign"}
            )
            assert other.status_code == 201
            update = {"project_id": other.json()["id"]}
        scope = NativeCacheScope.model_validate({**cache.scope.model_dump(), **update})
        denied = NativeResponsesCache(scope, http_client=api[0])
        with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
            denied.create(client, **request())
        assert not calls
    finally:
        client.close()


def test_full_native_request_and_freshness_revision_have_separate_cache_entries(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        changed = cache.create(client, **{**request(), "temperature": 0.1})
        assert changed.receipt.origin == "native_provider"
        scope = NativeCacheScope.model_validate(
            {**cache.scope.model_dump(), "freshness_ref": "owned-fixture-v2"}
        )
        fresh = NativeResponsesCache(scope, http_client=api[0]).create(client, **request())
        assert fresh.receipt.origin == "native_provider" and len(calls) == 3
    finally:
        client.close()


def test_revocation_during_native_call_blocks_retention_without_replaying_the_call(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    create = client.responses.create

    def revoked(**arguments):
        response = create(**arguments)
        with api[2].state.sessions.begin() as session:
            session.get(CapturePolicyRecord, cache.scope.policy_id).enabled = False
        return response

    monkeypatch.setattr(client.responses, "create", revoked)
    try:
        result = cache.create(client, **request())
        assert result.response.output_text == "PRIVATE_SYNTHETIC_CACHE_OUTPUT"
        assert result.receipt.cache_write == "rejected" and len(calls) == 1
        with api[2].state.sessions() as session:
            count = session.scalar(
                select(func.count())
                .select_from(CacheEntry)
                .where(CacheEntry.project_id == UUID(tenant[0]))
            )
        assert count == 0
        with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
            cache.create(client, **request())
        assert len(calls) == 1
    finally:
        client.close()


def expire_entry_at_full_shared_quota(api, tenant, cache):
    from cutback.server.evaluation_api import policy_object_count

    with api[2].state.sessions.begin() as session:
        row = session.scalar(select(CacheEntry).where(CacheEntry.project_id == UUID(tenant[0])))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        policy = session.get(CapturePolicyRecord, cache.scope.policy_id)
        policy.policy = {**policy.policy, "max_samples": policy_object_count(session, policy.id)}
        return row.id, row.object_ref


def test_expired_entry_refills_without_waiting_for_cleanup_or_consuming_another_slot(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        cache_id, old_ref = expire_entry_at_full_shared_quota(api, tenant, cache)
        refreshed = cache.create(client, **request())
        assert refreshed.receipt.cache_write == "created"
        cached = cache.create(client, **request())
        assert cached.receipt.origin == "encrypted_cache" and len(calls) == 2
        with api[2].state.sessions() as session:
            row = session.get(CacheEntry, cache_id)
            assert row is not None and row.object_ref != old_ref
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CacheEntry)
                    .where(CacheEntry.project_id == UUID(tenant[0]))
                )
                == 1
            )
        assert not api[2].state.artifact_store._path(tenant[0], old_ref).exists()
    finally:
        client.close()


def test_current_content_restrictions_block_old_cached_payload_before_provider_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        with api[2].state.sessions.begin() as session:
            policy = session.get(CapturePolicyRecord, cache.scope.policy_id)
            policy.policy = {
                **policy.policy,
                "never_capture_fields": [*policy.policy["never_capture_fields"], "text"],
            }
        with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
            cache.create(client, **request())
        assert len(calls) == 1
    finally:
        client.close()


def test_lost_refresh_commit_ack_preserves_the_committed_ciphertext_and_never_replays_native(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        cache_id, old_ref = expire_entry_at_full_shared_quota(api, tenant, cache)
        sessions = api[2].state.sessions
        original_begin = sessions.begin
        lost = []

        @contextmanager
        def lose_only_the_refresh_commit_ack():
            with original_begin() as session:
                yield session
            with sessions() as reader:
                current = reader.get(CacheEntry, cache_id)
                changed = current is not None and current.object_ref != old_ref
            if changed and not lost:
                lost.append(True)
                raise ConnectionError("OWNED_SYNTHETIC_LOST_CACHE_COMMIT_ACK")

        with monkeypatch.context() as fault:
            fault.setattr(sessions, "begin", lose_only_the_refresh_commit_ack)
            uncertain = cache.create(client, **request())
        assert lost and uncertain.receipt.cache_write == "uncertain" and len(calls) == 2
        replayed = cache.create(client, **request())
        assert replayed.receipt.origin == "encrypted_cache" and len(calls) == 2
        with sessions() as reader:
            current = reader.get(CacheEntry, cache_id)
            assert api[2].state.artifact_store._path(tenant[0], current.object_ref).exists()
        assert not api[2].state.artifact_store._path(tenant[0], old_ref).exists()
    finally:
        client.close()


def test_concurrent_expired_refreshes_preserve_one_slot_and_do_not_write_a_conflicting_object(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, _ = prepared(api, tenant, tmp_path, monkeypatch)
    original_post = cache._post
    bodies = []

    def capture(path, body):
        if path.endswith("put"):
            bodies.append(body)
        return original_post(path, body)

    monkeypatch.setattr(cache, "_post", capture)
    try:
        cache.create(client, **request())
        cache_id, old_ref = expire_entry_at_full_shared_quota(api, tenant, cache)
        store = api[2].state.artifact_store
        original_put = store.put
        writes = []

        def count_write(*args, **kwargs):
            ref = original_put(*args, **kwargs)
            writes.append(ref)
            return ref

        monkeypatch.setattr(store, "put", count_write)

        def refresh(_):
            return (
                api[0].post("/v1/ingest/cache/put", headers=tenant[1], json=bodies[0]).status_code
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(refresh, range(2)))
        assert sorted(statuses) == [201, 409] and len(writes) == 1
        with api[2].state.sessions() as session:
            assert session.get(CacheEntry, cache_id).object_ref == writes[0]
        assert not store._path(tenant[0], old_ref).exists()
    finally:
        client.close()


def test_failed_refresh_transaction_preserves_old_pointer_and_cleans_only_unreferenced_ciphertext(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        cache_id, old_ref = expire_entry_at_full_shared_quota(api, tenant, cache)
        sessions = api[2].state.sessions
        original_begin = sessions.begin
        store = api[2].state.artifact_store
        original_put = store.put
        writes, failed = [], []

        def record_put(*args, **kwargs):
            ref = original_put(*args, **kwargs)
            writes.append(ref)
            return ref

        @contextmanager
        def fail_before_refresh_commit():
            with original_begin() as session:
                yield session
                row = session.get(CacheEntry, cache_id)
                if row is not None and row.object_ref != old_ref and not failed:
                    failed.append(True)
                    raise ConnectionError("OWNED_SYNTHETIC_CACHE_TRANSACTION_ROLLBACK")

        with monkeypatch.context() as fault:
            fault.setattr(store, "put", record_put)
            fault.setattr(sessions, "begin", fail_before_refresh_commit)
            result = cache.create(client, **request())
        assert failed and result.receipt.cache_write == "uncertain" and len(calls) == 2
        with sessions() as reader:
            assert reader.get(CacheEntry, cache_id).object_ref == old_ref
        assert store._path(tenant[0], old_ref).exists()
        assert len(writes) == 1 and not store._path(tenant[0], writes[0]).exists()
    finally:
        client.close()


def test_unsettled_cache_writer_cannot_lose_its_uncommitted_ciphertext(
    api, tenant, tmp_path, monkeypatch
):
    from cutback.server.campaign_content import discard_unreferenced

    client, cache, _ = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        sessions = api[2].state.sessions
        store = api[2].state.artifact_store
        with sessions.begin() as writer:
            writer.get(CapturePolicyRecord, cache.scope.policy_id, with_for_update=True)
            row = writer.scalar(select(CacheEntry).where(CacheEntry.project_id == UUID(tenant[0])))
            old_ref = row.object_ref
            new_ref = store.put(UUID(tenant[0]), b"owned synthetic pending write", 1)
            row.object_ref = new_ref
            writer.flush()
            # A different connection cannot yet see the new reference. The
            # policy barrier must preserve it rather than trust that old view.
            discard_unreferenced(
                sessions, store, UUID(tenant[0]), [new_ref], policy_id=cache.scope.policy_id
            )
            assert store._path(tenant[0], new_ref).exists()
        discard_unreferenced(
            sessions, store, UUID(tenant[0]), [old_ref, new_ref], policy_id=cache.scope.policy_id
        )
        assert not store._path(tenant[0], old_ref).exists()
        assert store._path(tenant[0], new_ref).exists()
    finally:
        client.close()


@pytest.mark.parametrize("operation", ["invalidate", "expire"])
@pytest.mark.parametrize("commit_ack", ["rolled_back", "lost_after_commit"])
def test_cache_removal_deletes_ciphertext_only_after_reference_disappears(
    api, tenant, tmp_path, monkeypatch, operation, commit_ack
):
    from cutback.server.cache_api import expire_cache

    client, cache, _ = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        sessions = api[2].state.sessions
        store = api[2].state.artifact_store
        with sessions() as reader:
            row = reader.scalar(select(CacheEntry).where(CacheEntry.project_id == UUID(tenant[0])))
            cache_id, ref = row.id, row.object_ref
        original_begin = sessions.begin
        faults = []

        @contextmanager
        def failed_removal():
            with original_begin() as session:
                yield session
                session.flush()
                removed = session.get(CacheEntry, cache_id) is None
                if removed and not faults and commit_ack == "rolled_back":
                    faults.append(True)
                    raise ConnectionError("OWNED_SYNTHETIC_REMOVAL_ROLLBACK")
            if removed and not faults and commit_ack == "lost_after_commit":
                faults.append(True)
                raise ConnectionError("OWNED_SYNTHETIC_REMOVAL_COMMIT_ACK_LOST")

        with monkeypatch.context() as fault:
            fault.setattr(sessions, "begin", failed_removal)
            if operation == "invalidate":
                result = api[0].delete(f"/v1/projects/{tenant[0]}/cache/{cache_id}", headers=api[1])
                assert result.status_code == 500
            else:
                with pytest.raises(ConnectionError, match="OWNED_SYNTHETIC_REMOVAL"):
                    expire_cache(sessions, store, now=datetime.now(UTC) + timedelta(minutes=2))
        assert faults
        with sessions() as reader:
            remains = reader.get(CacheEntry, cache_id) is not None
        assert remains == (commit_ack == "rolled_back")
        assert store._path(tenant[0], ref).exists() == remains
        if remains:
            assert cache.create(client, **request()).receipt.origin == "encrypted_cache"
    finally:
        client.close()


def worker_cache(api, cache, *, actor="bootstrap_admin", authorize):
    from cutback.native_cache import NativeCacheContext
    from cutback.server.native_cache import WorkerNativeCache

    return WorkerNativeCache(
        NativeCacheContext.model_validate(cache.scope.model_dump(exclude={"endpoint", "token"})),
        api[2].state.sessions,
        api[2].state.artifact_store,
        actor=actor,
        authorize=authorize,
    )


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_server_cache_authority_cannot_be_coerced(api, tenant, tmp_path, monkeypatch, value):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        body = {
            "project_id": tenant[0],
            "policy_id": str(cache.scope.policy_id),
            "key_sha256": "a" * 64,
            "workflow": "classify",
            "environment": "staging",
            "provider": "openai",
            "model": "gpt-4.1",
            "prompt_revision": "owned-v1",
            "freshness_ref": "owned-v1",
            "response": "owned synthetic",
            "ttl_seconds": 60,
            "immutable_or_safely_invalidated": value,
        }
        response = api[0].post("/v1/ingest/cache/put", headers=tenant[1], json=body)
        assert response.status_code == 422 and not calls
    finally:
        client.close()


def test_missing_live_ciphertext_stops_before_another_native_dispatch(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        cache.create(client, **request())
        with api[2].state.sessions() as session:
            row = session.scalar(select(CacheEntry).where(CacheEntry.project_id == UUID(tenant[0])))
            api[2].state.artifact_store._path(tenant[0], row.object_ref).unlink()
        with pytest.raises(ValueError, match="CONTENT_UNAVAILABLE_NO_PROVIDER_REQUEST"):
            cache.create(client, **request())
        assert len(calls) == 1
    finally:
        client.close()


def test_wire_contract_does_not_change_existing_authenticated_cache_identity():
    from cutback.evals.datasets import digest
    from cutback.server.cache_api import CacheKey, key_digest

    legacy = {
        "project_id": "11111111-1111-4111-8111-111111111111",
        "policy_id": "22222222-2222-4222-8222-222222222222",
        "key_sha256": "a" * 64,
        "workflow": "owned-workflow",
        "environment": "staging",
        "provider": "openai",
        "model": "gpt-4.1",
        "prompt_revision": "owned-v1",
        "freshness_ref": "owned-immutable-v1",
    }
    assert key_digest(CacheKey.model_validate(legacy)) == digest(legacy)


@pytest.mark.parametrize("first_backend", ["http", "worker"])
def test_worker_and_http_share_encrypted_cache_without_internal_credentials(
    api, tenant, tmp_path, monkeypatch, first_backend
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    checks = []

    def authorize(session):
        checks.append(session)

    direct = worker_cache(api, cache, authorize=authorize)
    assert not hasattr(direct.scope, "token") and not hasattr(direct.scope, "endpoint")
    try:
        first, second = (cache, direct) if first_backend == "http" else (direct, cache)
        fresh = first.create(client, **request())
        hit = second.create(client, **request())
        assert fresh.receipt.cache_write == "created" and len(calls) == 1
        assert hit.receipt.origin == "encrypted_cache" and hit.receipt.native_sdk_calls == 0
        assert fresh.response.output_text == hit.response.output_text
        assert len(checks) == (1 if first_backend == "http" else 2)
    finally:
        client.close()


def test_direct_worker_rechecks_approving_principal_and_lease_even_on_cache_hits(
    api, tenant, tmp_path, monkeypatch
):
    client, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    created = api[0].post(
        f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": "project_admin"}
    )
    assert created.status_code == 201
    actor = created.json()["key_id"]
    authority = {"live": True}

    def authorize(session):
        if not authority["live"]:
            raise ValueError("OWNED_SYNTHETIC_WORKER_LEASE_EXPIRED")

    direct = worker_cache(api, cache, actor=actor, authorize=authorize)
    try:
        direct.create(client, **request())
        authority["live"] = False
        with pytest.raises(ValueError, match="WORKER_STORAGE_UNAVAILABLE"):
            direct.create(client, **request())
        authority["live"] = True
        revoked = api[0].delete(f"/v1/projects/{tenant[0]}/keys/{actor}", headers=api[1])
        assert revoked.status_code == 200
        with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
            direct.create(client, **request())
        assert len(calls) == 1
    finally:
        client.close()
