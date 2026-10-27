"""Private, atomic metadata checkpoints; no application imports or optional dependencies."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def private_directory(root: Path, relative: str) -> Path:
    from cutback.core.request_profiles import safe_relative

    safe_relative(relative)
    root = root.resolve(strict=True)
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("UNSAFE_WORKSPACE_DIRECTORY")
        current.mkdir(exist_ok=True, mode=0o700)
        if not current.resolve().is_relative_to(root):
            raise ValueError("UNSAFE_WORKSPACE_DIRECTORY")
    return current


def write_private(path: Path, value, *, exclusive=False):
    if path.is_symlink():
        raise ValueError("UNSAFE_WORKSPACE_FILE")
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, indent=2).encode() + b"\n"
    if len(raw) > 2097152:
        raise ValueError("WORKSPACE_METADATA_TOO_LARGE")
    temporary = path.with_name("." + path.name + "." + uuid4().hex)
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)
            temporary.unlink()
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_private(path: Path):
    if path.is_symlink() or path.stat().st_size > 2097152:
        raise ValueError("UNSAFE_WORKSPACE_FILE")
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def workspace_lock(directory: Path):
    path = directory / "operation.lock"
    if path.is_symlink():
        raise ValueError("UNSAFE_WORKSPACE_LOCK")
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    stream = os.fdopen(fd, "r+b")
    try:
        if os.fstat(fd).st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import importlib

                fcntl = importlib.import_module("fcntl")

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("WORKFLOW_BUSY_RETRY_STATUS") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import importlib

                fcntl = importlib.import_module("fcntl")

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        stream.close()
