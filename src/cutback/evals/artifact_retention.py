"""Bounded authenticated file expiry, including objects without database pointers.

Unexpired ownership is never inferred from a database snapshot. The sweep does
not modify relational pointers or extend encrypted retention. No paths, payloads
or object identities are included in its receipts.
"""

import os
import re
import stat
import time
from collections import deque
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict
from uuid import UUID

if TYPE_CHECKING:
    from cutback.evals.artifacts import EncryptedFileStore, EncryptedS3Store

Candidate = tuple[UUID, str, Path] | None | str


class ExpiryReceipt(TypedDict):
    backend: str
    scanned: int
    deleted: int
    failures: int
    cycle_complete: bool
    deletion_basis: str
    unexpired_orphans_deleted: bool


def validate_bounds(limit, max_seconds):
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(max_seconds) not in {int, float}
        or not 0 < max_seconds <= 5
    ):
        raise ValueError("ENCRYPTED_EXPIRY_SWEEP_BOUND_REQUIRED")


def expiry_receipt(backend) -> ExpiryReceipt:
    return {
        "backend": backend,
        "scanned": 0,
        "deleted": 0,
        "failures": 0,
        "cycle_complete": False,
        "deletion_basis": "authenticated_expiry",
        "unexpired_orphans_deleted": False,
    }


class FileExpirySweep:
    def __init__(self, store: "EncryptedFileStore"):
        self.store = store
        self.iterator: Iterator[Candidate] | None = None

    def close(self):
        iterator, self.iterator = self.iterator, None
        if iterator is not None:
            close = getattr(iterator, "close", None)
            if close:
                close()

    def candidates(self) -> Iterator[Candidate]:
        with os.scandir(self.store.root) as projects:
            for directory in projects:
                yield None  # Directory traversal also consumes the page bound.
                if not directory.is_dir(follow_symlinks=False):
                    continue
                try:
                    project = UUID(directory.name)
                    if str(project) != directory.name:
                        continue
                except ValueError:
                    continue
                try:
                    with os.scandir(directory.path) as objects:
                        for entry in objects:
                            if not entry.is_file(follow_symlinks=False):
                                yield None
                                continue
                            name, separator, temporary = entry.name.partition(".rekey-")
                            try:
                                ref = str(UUID(name))
                            except ValueError:
                                yield None
                                continue
                            if (
                                ref != name
                                or separator
                                and not re.fullmatch("[a-f0-9]{32}", temporary)
                            ):
                                yield None
                                continue
                            yield project, ref, Path(entry.path)
                except OSError:
                    yield "DIRECTORY_UNAVAILABLE"

    def page(self, *, limit=100, max_seconds=2.0):
        validate_bounds(limit, max_seconds)
        receipt = expiry_receipt("file")
        if self.iterator is None:
            self.iterator = self.candidates()
        deadline = time.monotonic() + max_seconds
        for _ in range(limit):
            if time.monotonic() >= deadline:
                break
            try:
                candidate = next(self.iterator)
            except StopIteration:
                self.close()
                receipt["cycle_complete"] = True
                break
            except Exception:
                self.close()
                receipt["failures"] += 1
                break
            receipt["scanned"] += 1
            if candidate is None:
                continue
            if isinstance(candidate, str):
                receipt["failures"] += 1
                continue
            project, ref, path = candidate
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                if (
                    path.is_symlink()
                    or path.parent.is_symlink()
                    or not path.resolve().is_relative_to(self.store.root)
                ):
                    raise ValueError("UNSAFE_ARTIFACT_PATH")
                with os.fdopen(os.open(path, flags), "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size > 1024000:
                        raise ValueError("UNSAFE_OR_OVERSIZED_ARTIFACT")
                    raw = stream.read(1024001)
                    _, expires = self.store.authenticated_payload(project, ref, raw)
                if expires > datetime.now(UTC):
                    continue
                # A live rekey cannot replace expired ciphertext: its read would
                # already refuse PAYLOAD_EXPIRED. Preserve any changed identity.
                after = path.lstat()
                if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ) or not stat.S_ISREG(after.st_mode):
                    raise ValueError("ARTIFACT_CHANGED_DURING_EXPIRY")
                path.unlink()
                receipt["deleted"] += 1
            except FileNotFoundError:
                pass  # A concurrent authorized deletion has already completed.
            except Exception:
                receipt["failures"] += 1
        return receipt


class S3ExpirySweep:
    """Authenticate exact object versions; never remove a delete marker.

    Versioned buckets require list/read/delete-version permissions. Conditional
    deletion is mandatory, including suspended buckets' mutable null versions.
    Unsupported permissions or conditional-delete APIs preserve content and fail
    the maintenance phase; they never fall back to an unconditional delete.
    """

    def __init__(self, store: "EncryptedS3Store"):
        self.store = store
        self.pending: deque[dict[str, Any]] = deque()
        self.cursor: dict[str, str] = {}
        self.versioned: bool | None = None
        self.last_page = False

    def close(self):
        self.pending.clear()
        self.cursor = {}
        self.versioned = None
        self.last_page = False

    def load(self, limit):
        client, bucket = self.store.client, self.store.bucket
        if self.versioned is None:
            status = client.get_bucket_versioning(Bucket=bucket).get("Status")
            if status not in {None, "Enabled", "Suspended"}:
                raise ValueError("UNKNOWN_BUCKET_VERSIONING_STATE")
            self.versioned = status in {"Enabled", "Suspended"}
        if self.versioned:
            reply = client.list_object_versions(Bucket=bucket, MaxKeys=limit, **self.cursor)
            versions, markers = reply.get("Versions", []), reply.get("DeleteMarkers", [])
            if not isinstance(versions, list) or not isinstance(markers, list):
                raise ValueError("INVALID_OBJECT_VERSION_PAGE")
            entries = [{**item, "_delete_marker": False} for item in versions]
            entries += [{**item, "_delete_marker": True} for item in markers]
            cursor = {
                "KeyMarker": reply.get("NextKeyMarker"),
                "VersionIdMarker": reply.get("NextVersionIdMarker"),
            }
        else:
            reply = client.list_objects_v2(Bucket=bucket, MaxKeys=limit, **self.cursor)
            entries = reply.get("Contents", [])
            cursor = {"ContinuationToken": reply.get("NextContinuationToken")}
        if (
            not isinstance(entries, list)
            or len(entries) > limit
            or type(reply.get("IsTruncated")) is not bool
        ):
            raise ValueError("INVALID_BOUNDED_OBJECT_PAGE")
        if reply["IsTruncated"]:
            if (
                any(
                    not isinstance(value, str) or not 0 < len(value) <= 4096
                    for value in cursor.values()
                )
                or cursor == self.cursor
            ):
                raise ValueError("INVALID_OBJECT_PAGE_CONTINUATION")
        else:
            cursor = {}
        if any(not isinstance(item, dict) for item in entries):
            raise ValueError("INVALID_OBJECT_PAGE_MEMBER")
        self.cursor = cursor
        self.last_page = not reply["IsTruncated"]
        self.pending.extend(entries)

    def page(self, *, limit=100, max_seconds=2.0):
        validate_bounds(limit, max_seconds)
        receipt = expiry_receipt("s3")
        deadline = time.monotonic() + max_seconds
        if not self.pending:
            try:
                self.load(limit)
            except Exception:
                self.close()
                receipt["failures"] = 1
                return receipt
        for _ in range(limit):
            if not self.pending or time.monotonic() >= deadline:
                break
            item = self.pending.popleft()
            receipt["scanned"] += 1
            if item.get("_delete_marker"):
                continue  # Removing it could resurrect an unexpired old version.
            try:
                key = item.get("Key")
                if not isinstance(key, str) or len(key) > 73:
                    continue
                parts = key.split("/")
                if len(parts) != 2:
                    continue
                project, ref = UUID(parts[0]), str(UUID(parts[1]))
                if key != f"{project}/{ref}":
                    continue
                version = item.get("VersionId") if self.versioned else None
                if self.versioned and (
                    not isinstance(version, str) or not 0 < len(version) <= 1024
                ):
                    raise ValueError("OBJECT_VERSION_ID_REQUIRED")
                arguments = {"Bucket": self.store.bucket, "Key": key}
                if version is not None:
                    arguments["VersionId"] = version
                reply = self.store.client.get_object(**arguments)
                body = reply["Body"]
                try:
                    raw = body.read(1024001)
                finally:
                    body.close()
                _, expires = self.store.authenticated_payload(project, ref, raw)
                if expires > datetime.now(UTC):
                    continue
                etag = reply.get("ETag")
                if not isinstance(etag, str) or not 0 < len(etag) <= 1024:
                    raise ValueError("CONDITIONAL_DELETE_ETAG_REQUIRED")
                actual_version = reply.get("VersionId")
                if version is not None and actual_version != version:
                    raise ValueError("OBJECT_VERSION_CHANGED")
                if actual_version is not None:
                    if not isinstance(actual_version, str) or not 0 < len(actual_version) <= 1024:
                        raise ValueError("INVALID_OBJECT_VERSION_ID")
                    arguments["VersionId"] = actual_version
                self.store.client.delete_object(**arguments, IfMatch=etag)
                receipt["deleted"] += 1
            except Exception:
                receipt["failures"] += 1
        if not self.pending and self.last_page:
            self.close()
            receipt["cycle_complete"] = True
        return receipt
