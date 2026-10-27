import json
import os
import shutil
from uuid import UUID

import pytest
from cryptography.exceptions import InvalidTag
from support_evaluation import authorized_dataset

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import PayloadSample

pytestmark = pytest.mark.postgres


def test_restored_objects_and_rotated_keys_preserve_database_pointers(
    api, tenant, tmp_path, monkeypatch
):
    _, _, app = api
    old_key = os.urandom(32)
    source = EncryptedFileStore(tmp_path / "original-objects", old_key)
    _, store, _, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch, store=source)
    restored = tmp_path / "restored-objects"
    shutil.copytree(store.root, restored)
    new_key = os.urandom(32)
    rotating = EncryptedFileStore(restored, new_key, previous_keys=(old_key,))
    new_only = EncryptedFileStore(restored, new_key)
    with app.state.sessions() as session:
        rows = (
            session.query(PayloadSample).filter(PayloadSample.project_id == UUID(tenant[0])).all()
        )
        assert rows
        for row in rows:
            original = store.get(UUID(tenant[0]), row.object_ref)
            before_header = json.loads(
                rotating._path(UUID(tenant[0]), row.object_ref).read_bytes()
            )["header"]
            rotating.rekey(UUID(tenant[0]), row.object_ref)
            assert new_only.get(UUID(tenant[0]), row.object_ref) == original
            assert (
                json.loads(rotating._path(UUID(tenant[0]), row.object_ref).read_bytes())["header"]
                == before_header
            )
            rotating.rekey(UUID(tenant[0]), row.object_ref)
            assert new_only.get(UUID(tenant[0]), row.object_ref) == original
            with pytest.raises((ValueError, FileNotFoundError)):
                new_only.get(UUID(int=1), row.object_ref)
            path = rotating._path(UUID(tenant[0]), row.object_ref)
            envelope = json.loads(path.read_bytes())
            envelope["ciphertext"] = envelope["ciphertext"][:-8] + "AAAAAAAA"
            path.write_text(json.dumps(envelope))
            with pytest.raises(InvalidTag):
                new_only.get(UUID(tenant[0]), row.object_ref)
