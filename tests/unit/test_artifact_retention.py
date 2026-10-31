"""Expired encrypted orphans are removed without guessing uncommitted ownership."""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from support_artifact_retention import drain, expired_object

from cutback.evals.artifacts import EncryptedFileStore


def test_authenticated_expiry_removes_orphan_and_rekey_temporary_but_preserves_live(tmp_path):
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    project = uuid4()
    _, orphan = expired_object(store, project)
    ref, temporary = expired_object(store, project)
    temporary = temporary.rename(temporary.with_name(ref + ".rekey-" + uuid4().hex))
    live = store.put(project, b"uncommitted or referenced ownership stays unknown", 1)
    receipts = drain(store, limit=1)
    assert all(item["scanned"] <= 1 for item in receipts)
    assert sum(item["deleted"] for item in receipts) == 2
    assert not orphan.exists() and not temporary.exists()
    assert store.get(project, live) == b"uncommitted or referenced ownership stays unknown"
    assert all(not item["unexpired_orphans_deleted"] for item in receipts)


def test_forged_expiry_wrong_project_corrupt_and_oversized_objects_are_preserved(tmp_path):
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    project = uuid4()
    live = store.put(project, b"PRIVATE synthetic content", 1)
    path = store._path(project, live)
    envelope = json.loads(path.read_bytes())
    header = json.loads(base64.b64decode(envelope["header"]))
    header["expires"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    envelope["header"] = base64.b64encode(json.dumps(header).encode()).decode()
    path.write_text(json.dumps(envelope))
    _, other = expired_object(store, uuid4())
    other = other.rename(store._path(project, other.name))
    corrupt = store._path(project, str(uuid4()))
    corrupt.write_bytes(b"invalid private synthetic object")
    oversized = store._path(project, str(uuid4()))
    oversized.write_bytes(b"x" * 1024001)
    receipts = drain(store, limit=2)
    assert sum(item["deleted"] for item in receipts) == 0
    assert sum(item["failures"] for item in receipts) == 4
    assert all(item.exists() for item in (path, other, corrupt, oversized))
    assert "PRIVATE" not in json.dumps(receipts) and live not in json.dumps(receipts)


def test_previous_encryption_key_expiry_and_iterator_restart(tmp_path):
    old_key, new_key = os.urandom(32), os.urandom(32)
    old = EncryptedFileStore(tmp_path, old_key)
    project = uuid4()
    _, expired = expired_object(old, project)
    current = EncryptedFileStore(tmp_path, new_key, previous_keys=(old_key,))
    assert sum(item["deleted"] for item in drain(current)) == 1 and not expired.exists()
    _, later = expired_object(current, project)
    assert sum(item["deleted"] for item in drain(current)) == 1 and not later.exists()
    current.close()
    assert store_iteration_closed(current)


def store_iteration_closed(store):
    return store._expiry_sweep.iterator is None


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_sweep_enforces_finite_integer_page_limit(tmp_path, limit):
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    with pytest.raises(ValueError, match="SWEEP_BOUND"):
        store.expire_page(limit=limit)


def test_changed_ciphertext_during_authentication_is_not_deleted(tmp_path, monkeypatch):
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    project = uuid4()
    ref, expired = expired_object(store, project)
    authenticate = store.authenticated_payload

    def replace_after_read(project_id, object_ref, raw):
        result = authenticate(project_id, object_ref, raw)
        expired.write_bytes(store.encrypt(project, ref, b"new authorized content", 1))
        return result

    monkeypatch.setattr(store, "authenticated_payload", replace_after_read)
    receipts = drain(store)
    assert sum(item["deleted"] for item in receipts) == 0
    assert sum(item["failures"] for item in receipts) == 1
    assert expired.exists()


def test_sweep_does_not_traverse_symlink_or_non_project_directory(tmp_path):
    store = EncryptedFileStore(tmp_path / "store", os.urandom(32))
    outside = EncryptedFileStore(tmp_path / "outside", os.urandom(32))
    project = uuid4()
    _, preserved = expired_object(outside, project)
    (store.root / "not-a-project").mkdir()
    (store.root / "not-a-project" / str(uuid4())).write_bytes(preserved.read_bytes())
    try:
        (store.root / str(project)).symlink_to(
            outside.root / str(project), target_is_directory=True
        )
    except OSError:
        pytest.skip("host does not permit creating test directory symlinks")
    assert sum(item["deleted"] for item in drain(store, limit=1)) == 0
    assert preserved.exists()


def test_unknown_key_is_preserved_and_time_budget_stops_page(tmp_path, monkeypatch):
    import cutback.evals.artifact_retention as retention

    old = EncryptedFileStore(tmp_path, os.urandom(32))
    _, path = expired_object(old, uuid4())
    current = EncryptedFileStore(tmp_path, os.urandom(32))
    receipts = drain(current)
    assert path.exists() and sum(item["failures"] for item in receipts) == 1
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr(retention.time, "monotonic", lambda: next(ticks))
    receipt = current.expire_page(limit=100, max_seconds=2)
    assert receipt["scanned"] == 0 and not receipt["cycle_complete"]
    current.close()
