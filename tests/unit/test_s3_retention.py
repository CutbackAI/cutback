"""Synthetic S3 protocol: exact version expiry and conditional deletion only."""

import hashlib
import io
import os
from uuid import uuid4

import pytest
from support_artifact_retention import drain, expired_object

from cutback.evals.artifacts import EncryptedFileStore, EncryptedS3Store


class SyntheticS3:
    def __init__(self, *, versioned=False):
        self.versioned = versioned
        self.objects = {}
        self.markers = []
        self.deletes = []
        self.list_calls = []
        self.permission_denied = False
        self.change_before_delete = False
        self.snapshot = None

    def get_bucket_versioning(self, *, Bucket):
        if self.permission_denied:
            raise PermissionError("owned private synthetic bucket label")
        return {"Status": "Enabled"} if self.versioned else {}

    def list_objects_v2(self, *, Bucket, MaxKeys, ContinuationToken="0"):
        self.list_calls.append(MaxKeys)
        offset = int(ContinuationToken)
        if offset == 0:
            self.snapshot = [{"Key": key} for key, _ in self.objects]
        all_items = self.snapshot
        page = all_items[offset : offset + MaxKeys]
        more = offset + MaxKeys < len(all_items)
        return {
            "Contents": page,
            "IsTruncated": more,
            **({"NextContinuationToken": str(offset + MaxKeys)} if more else {}),
        }

    def list_object_versions(self, *, Bucket, MaxKeys, KeyMarker="0", VersionIdMarker="0"):
        self.list_calls.append(MaxKeys)
        offset = int(KeyMarker)
        if offset == 0:
            self.snapshot = [{"Key": key, "VersionId": version} for key, version in self.objects]
            self.snapshot += [{**item, "_marker": True} for item in self.markers]
        all_items = self.snapshot
        page = all_items[offset : offset + MaxKeys]
        more = offset + MaxKeys < len(all_items)
        return {
            "Versions": [item for item in page if not item.get("_marker")],
            "DeleteMarkers": [item for item in page if item.get("_marker")],
            "IsTruncated": more,
            **(
                {"NextKeyMarker": str(offset + MaxKeys), "NextVersionIdMarker": "0"} if more else {}
            ),
        }

    def get_object(self, *, Bucket, Key, VersionId=None):
        blob = self.objects[(Key, VersionId)]
        return {
            "Body": io.BytesIO(blob),
            "ETag": hashlib.sha256(blob).hexdigest(),
            **({"VersionId": VersionId} if VersionId is not None else {}),
        }

    def delete_object(self, *, Bucket, Key, IfMatch, VersionId=None):
        if self.change_before_delete:
            self.objects[(Key, VersionId)] = b"new concurrent object must survive"
        current = self.objects[(Key, VersionId)]
        if hashlib.sha256(current).hexdigest() != IfMatch:
            raise ValueError("PreconditionFailed")
        self.deletes.append((Key, VersionId))
        del self.objects[(Key, VersionId)]


def fixture(tmp_path, *, versioned=False):
    key, project = os.urandom(32), uuid4()
    local = EncryptedFileStore(tmp_path, key)
    ref, path = expired_object(local, project)
    client = SyntheticS3(versioned=versioned)
    store = EncryptedS3Store("owned-synthetic-only", key, client=client)
    return store, client, local, project, ref, path.read_bytes()


def test_s3_unversioned_expiry_uses_conditional_delete_and_preserves_live(tmp_path):
    store, client, local, project, ref, expired = fixture(tmp_path)
    client.objects[(f"{project}/{ref}", None)] = expired
    live = str(uuid4())
    client.objects[(f"{project}/{live}", None)] = local.encrypt(
        project, live, b"PRIVATE live object", 1
    )
    receipts = drain(store, limit=2)
    assert sum(item["deleted"] for item in receipts) == 1
    assert client.deletes == [(f"{project}/{ref}", None)]
    assert (f"{project}/{live}", None) in client.objects
    assert all(value <= 2 for value in client.list_calls)
    assert "PRIVATE" not in str(receipts) and ref not in str(receipts)


def test_s3_all_expired_versions_are_deleted_without_removing_markers_or_live_versions(tmp_path):
    store, client, local, project, ref, expired = fixture(tmp_path, versioned=True)
    name = f"{project}/{ref}"
    client.objects[(name, "old-1")] = expired
    client.objects[(name, "old-2")] = expired
    client.objects[(name, "current-live")] = local.encrypt(project, ref, b"live current", 1)
    client.markers.append({"Key": name, "VersionId": "preserved-delete-marker"})
    # One page contains the full immutable listing, allowing bounded resume.
    receipts = drain(store, limit=100)
    assert sum(item["deleted"] for item in receipts) == 2
    assert set(client.deletes) == {(name, "old-1"), (name, "old-2")}
    assert (name, "current-live") in client.objects and len(client.markers) == 1


def test_s3_changed_null_version_is_not_deleted(tmp_path):
    store, client, _, project, ref, expired = fixture(tmp_path, versioned=True)
    client.objects[(f"{project}/{ref}", "null")] = expired
    client.change_before_delete = True
    receipts = drain(store)
    assert sum(item["deleted"] for item in receipts) == 0
    assert sum(item["failures"] for item in receipts) == 1
    assert client.objects and not client.deletes


@pytest.mark.parametrize("versioned", [False, True])
def test_s3_continuation_does_not_skip_later_expired_objects(tmp_path, versioned):
    store, client, local, project, ref, expired = fixture(tmp_path, versioned=versioned)
    for index in range(5):
        item = f"owned-version-{index}" if versioned else None
        key = f"{project}/{ref}" if versioned else f"{project}/{uuid4()}"
        if not versioned:
            _, path = expired_object(local, project, ref=key.split("/")[1])
            expired = path.read_bytes()
        client.objects[(key, item)] = expired
    receipts = drain(store, limit=1)
    assert sum(item["deleted"] for item in receipts) == 5
    assert all(item["scanned"] <= 1 for item in receipts) and not client.objects


@pytest.mark.parametrize(
    "fault", ["permission", "tamper", "missing_etag", "wrong_version", "missing_cursor"]
)
def test_s3_uncertain_or_unverified_objects_are_preserved(tmp_path, monkeypatch, fault):
    store, client, _, project, ref, expired = fixture(tmp_path, versioned=True)
    name = f"{project}/{ref}"
    client.objects[(name, "owned-old-version")] = expired
    if fault == "permission":
        client.permission_denied = True
    elif fault == "tamper":
        client.objects[(name, "owned-old-version")] = expired[:-4] + b"AAAA"
    elif fault == "missing_cursor":
        monkeypatch.setattr(
            client, "list_object_versions", lambda **kwargs: {"Versions": [], "IsTruncated": True}
        )
    else:
        get = client.get_object

        def invalid(**kwargs):
            result = get(**kwargs)
            if fault == "missing_etag":
                result.pop("ETag")
            else:
                result["VersionId"] = "wrong-version"
            return result

        monkeypatch.setattr(client, "get_object", invalid)
    receipt = store.expire_page()
    assert receipt["failures"] == 1 and receipt["deleted"] == 0
    assert client.objects and not client.deletes


def test_installed_s3_sdk_accepts_conditional_version_deletion_parameters():
    from botocore.session import get_session
    from botocore.validate import validate_parameters

    model = get_session().get_service_model("s3")
    validate_parameters(
        {
            "Bucket": "owned-synthetic-only",
            "Key": f"{uuid4()}/{uuid4()}",
            "VersionId": "owned-old-version",
            "IfMatch": '"owned-etag"',
        },
        model.operation_model("DeleteObject").input_shape,
    )
