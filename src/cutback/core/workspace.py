"""Private, atomic metadata checkpoints; no application imports or optional dependencies."""

import importlib
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
    # A fixed-length temporary name avoids repeating a long destination name
    # in deep Windows application workspaces. O_EXCL still prevents reuse.
    temporary = path.with_name(".cutback-write-" + uuid4().hex)
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


def prepare_private_output(path: Path) -> Path:
    """Validate the private destination before requesting content; recheck when writing."""
    import stat

    path = Path(os.path.abspath(path))
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            parent.mkdir(mode=0o700)
            info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 1024
        ):
            raise ValueError("UNSAFE_PRIVATE_OUTPUT_DIRECTORY")
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 1024
        ):
            raise ValueError("UNSAFE_PRIVATE_OUTPUT_FILE")
    return path


def write_private_output(path: Path, value):
    """Write explicitly requested private JSON without following redirected parents."""
    path = prepare_private_output(path)
    write_private(path, value)


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
                msvcrt = importlib.import_module("msvcrt")

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("WORKFLOW_BUSY_RETRY_STATUS") from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt = importlib.import_module("msvcrt")

                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl = importlib.import_module("fcntl")

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        stream.close()
