"""AES-GCM encrypted payloads. Project, expiry and reference are authenticated data."""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4


class ArtifactStore(Protocol):
    def put(self, project: UUID, blob: bytes, ttl_days: int) -> str: ...
    def get(self, project: UUID, ref: str) -> bytes: ...
    def delete(self, project: UUID, ref: str) -> None: ...


class PayloadCodec:
    def __init__(self, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(key) != 32:
            raise ValueError("payload encryption requires a 32-byte key")
        self.cipher = AESGCM(key)

    def encrypt(self, project, ref, blob, ttl_days):
        if not 1 <= ttl_days <= 30 or len(blob) > 512000:
            raise ValueError("payload size or retention exceeds policy")
        header = json.dumps(
            {
                "project": str(project),
                "ref": ref,
                "expires": (datetime.now(UTC) + timedelta(days=ttl_days)).isoformat(),
            },
            sort_keys=True,
        ).encode()
        nonce = os.urandom(12)
        encrypted = self.cipher.encrypt(nonce, blob, header)
        return json.dumps(
            {
                "header": base64.b64encode(header).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(encrypted).decode(),
            }
        ).encode()

    def decrypt(self, project, ref, raw):
        if len(raw) > 1024000:
            raise ValueError("oversized encrypted artifact")
        envelope = json.loads(raw)
        header = base64.b64decode(envelope["header"], validate=True)
        metadata = json.loads(header)
        if metadata["project"] != str(project) or metadata["ref"] != str(UUID(ref)):
            raise ValueError("artifact scope mismatch")
        plaintext = self.cipher.decrypt(
            base64.b64decode(envelope["nonce"], validate=True),
            base64.b64decode(envelope["ciphertext"], validate=True),
            header,
        )
        if datetime.fromisoformat(metadata["expires"]) <= datetime.now(UTC):
            raise ValueError("PAYLOAD_EXPIRED")
        return plaintext


class EncryptedFileStore(PayloadCodec):
    def __init__(self, root: Path, key: bytes):
        super().__init__(key)
        if root.is_symlink():
            raise ValueError("payload root must not be a symlink")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, project, ref):
        project, ref = UUID(str(project)), UUID(str(ref))
        directory = self.root / str(project)
        if directory.is_symlink():
            raise ValueError("unsafe artifact directory")
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / str(ref)
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError("unsafe artifact reference")
        return path

    def put(self, project: UUID, blob: bytes, ttl_days: int) -> str:
        ref = str(uuid4())
        encrypted = self.encrypt(project, ref, blob, ttl_days)
        fd = os.open(self._path(project, ref), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        return ref

    def get(self, project: UUID, ref: str) -> bytes:
        path = self._path(project, ref)
        if path.stat().st_size > 1024000:
            raise ValueError("oversized encrypted artifact")
        return self.decrypt(project, ref, path.read_bytes())

    def delete(self, project: UUID, ref: str):
        self._path(project, ref).unlink(missing_ok=True)


class EncryptedS3Store(PayloadCodec):
    def __init__(self, bucket: str, key: bytes, *, client=None, endpoint=None, region=None):
        super().__init__(key)
        if not bucket or len(bucket) > 255:
            raise ValueError("customer bucket required")
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            from cutback.core.config import validate_endpoint

            client = boto3.client(
                "s3",
                region_name=region,
                endpoint_url=validate_endpoint(endpoint) if endpoint else None,
                config=Config(
                    connect_timeout=3,
                    read_timeout=3,
                    retries={"max_attempts": 0},
                    s3={"addressing_style": "path"},
                ),
            )
        self.client = client

    def _key(self, project, ref):
        return f"{UUID(str(project))}/{UUID(str(ref))}"

    def put(self, project: UUID, blob: bytes, ttl_days: int) -> str:
        ref = str(uuid4())
        key = self._key(project, ref)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=self.encrypt(project, ref, blob, ttl_days),
            ContentType="application/octet-stream",
        )
        return ref

    def get(self, project: UUID, ref: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=self._key(project, ref))
        body = response["Body"]
        try:
            raw = body.read(1024001)
        finally:
            body.close()
        return self.decrypt(project, ref, raw)

    def delete(self, project: UUID, ref: str):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(project, ref))


def configured_store() -> ArtifactStore:
    root, key = os.getenv("CUTBACK_PAYLOAD_DIR"), os.getenv("CUTBACK_PAYLOAD_KEY")
    if not key:
        raise ValueError("PAYLOAD_STORAGE_DISABLED: configure directory and encryption key")
    if os.getenv("CUTBACK_PAYLOAD_BACKEND", "file") not in {"file", "s3"}:
        raise ValueError("unsupported payload backend")
    if os.getenv("CUTBACK_PAYLOAD_BACKEND", "file") == "s3":
        return EncryptedS3Store(
            os.environ["CUTBACK_S3_BUCKET"],
            base64.b64decode(key, validate=True),
            endpoint=os.getenv("CUTBACK_S3_ENDPOINT"),
            region=os.getenv("CUTBACK_S3_REGION"),
        )
    if not root:
        raise ValueError("PAYLOAD_STORAGE_DISABLED: configure a payload directory")
    return EncryptedFileStore(Path(root), base64.b64decode(key, validate=True))
