"""Real PostgreSQL references never authorize deleting uncertain live ciphertext."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from support_artifact_retention import drain, expired_object
from test_native_cache_workflow import prepared, request

from cutback.server.db import PayloadSample
from cutback.server.enterprise_db import CacheEntry

pytestmark = pytest.mark.postgres


def test_expired_orphan_and_unavailable_cache_cleanup_preserve_dataset_and_call_boundary(
    api, tenant, tmp_path, monkeypatch
):
    native, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    store, project = api[2].state.artifact_store, UUID(tenant[0])
    try:
        cache.create(native, **request())
        with api[2].state.sessions() as session:
            row = session.scalar(select(CacheEntry).where(CacheEntry.project_id == project))
            assert row is not None
            cache_ref, cache_id = row.object_ref, row.id
            payload_refs = session.scalars(
                select(PayloadSample.object_ref).where(PayloadSample.project_id == project)
            ).all()
        before = {ref: store.get(project, ref) for ref in payload_refs}
        expired_object(store, project, ref=cache_ref)
        _, orphan = expired_object(store, project)
        receipts = drain(store, limit=2)
        assert sum(item["deleted"] for item in receipts) == 2 and not orphan.exists()
        assert {ref: store.get(project, ref) for ref in payload_refs} == before
        with pytest.raises(ValueError, match="CONTENT_UNAVAILABLE_NO_PROVIDER_REQUEST"):
            cache.create(native, **request())
        assert len(calls) == 1
        with api[2].state.sessions() as session:
            assert session.get(CacheEntry, cache_id).object_ref == cache_ref
    finally:
        native.close()
        store.close()


def test_sweep_preserves_live_object_while_its_real_database_transaction_is_uncommitted(
    api, tenant, tmp_path, monkeypatch
):
    native, cache, _ = prepared(api, tenant, tmp_path, monkeypatch)
    store, project = api[2].state.artifact_store, UUID(tenant[0])
    value = b"owned uncommitted synthetic object"
    ref = store.put(project, value, 1)
    try:
        with api[2].state.sessions.begin() as session:
            row = PayloadSample(
                project_id=project,
                policy_id=cache.scope.policy_id,
                object_ref=ref,
                content_hash="f" * 64,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                metadata_safe={"kind": "owned_inflight_fixture"},
            )
            session.add(row)
            session.flush()
            receipt = drain(store)
            assert sum(item["deleted"] for item in receipt) == 0
            assert store.get(project, ref) == value
            row_id = row.id
        with api[2].state.sessions() as session:
            assert session.get(PayloadSample, row_id).object_ref == ref
        assert store.get(project, ref) == value
    finally:
        native.close()
        store.close()
