"""Refuse publication from uncommitted inputs or bytes hidden by Git text filters.

Read-only, bounded, stdlib-only. Reports counts, never source, names or Git stderr.
"""

import argparse
import json
import os
import stat
import subprocess
from pathlib import Path

MAX_FILES = 10000
MAX_BYTES = 128 * 1048576


def git(root, *arguments, source=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    result = subprocess.run(
        ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        input=source,
        capture_output=True,
        timeout=30,
        env=environment,
    )
    if result.returncode:
        raise ValueError("SOURCE_SNAPSHOT_GIT_UNAVAILABLE_OR_INVALID")
    return result.stdout


def check(root):
    root = Path(root).resolve(strict=True)
    top = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
    if root != top:
        raise ValueError("SOURCE_SNAPSHOT_REQUIRES_EXACT_REPOSITORY_ROOT")
    records = [row for row in git(root, "ls-tree", "-rz", "HEAD").split(b"\0") if row]
    if not records or len(records) > MAX_FILES:
        raise ValueError("SOURCE_SNAPSHOT_FILE_BOUND")
    entries = []
    for row in records:
        header, name = row.split(b"\t", 1)
        mode, kind, identifier = header.split()
        if mode not in {b"100644", b"100755"} or kind != b"blob":
            raise ValueError("SOURCE_SNAPSHOT_LINK_OR_SUBMODULE_UNQUALIFIED")
        entries.append((identifier, name.decode("utf-8")))
    identifiers = b"\n".join(identifier for identifier, _ in entries) + b"\n"
    sizes = git(root, "cat-file", "--batch-check", source=identifiers).splitlines()
    if len(sizes) != len(entries):
        raise ValueError("SOURCE_SNAPSHOT_OBJECT_METADATA_INVALID")
    total = 0
    for row in sizes:
        _, kind, size = row.split()
        if kind != b"blob" or not size.isdigit():
            raise ValueError("SOURCE_SNAPSHOT_OBJECT_METADATA_INVALID")
        total += int(size)
        if total > MAX_BYTES:
            raise ValueError("SOURCE_SNAPSHOT_BYTE_BOUND")
    blobs = git(root, "cat-file", "--batch", source=identifiers)
    different = missing = unsafe = offset = 0
    safe_parents = set()
    for identifier, name in entries:
        end = blobs.index(b"\n", offset)
        found, kind, size = blobs[offset:end].split()
        if found != identifier or kind != b"blob":
            raise ValueError("SOURCE_SNAPSHOT_OBJECT_CONTENT_INVALID")
        start, length = end + 1, int(size)
        expected = blobs[start : start + length]
        offset = start + length + 1
        path = root / name
        if path.is_absolute() and not path.is_relative_to(root):
            raise ValueError("SOURCE_SNAPSHOT_PATH_INVALID")
        try:
            # Junctions and symlinks cannot redirect a release check outside its checkout.
            for part in (path, *path.parents):
                if part == root:
                    break
                if part in safe_parents:
                    break
                info = part.lstat()
                if part == path and not stat.S_ISREG(info.st_mode):
                    raise ValueError("SOURCE_SNAPSHOT_UNSAFE_FILESYSTEM_PATH")
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                    raise ValueError("SOURCE_SNAPSHOT_UNSAFE_FILESYSTEM_PATH")
                if part != path:
                    safe_parents.add(part)
            if path.stat().st_size != length or path.read_bytes() != expected:
                different += 1
        except FileNotFoundError:
            missing += 1
        except ValueError:
            unsafe += 1
    if offset != len(blobs):
        raise ValueError("SOURCE_SNAPSHOT_OBJECT_CONTENT_INVALID")
    staged = bool(
        git(root, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--name-only", "-z")
    )
    untracked = len(
        [
            p
            for p in git(
                root,
                "ls-files",
                "--others",
                "--exclude-per-directory=.gitignore",
                "--directory",
                "-z",
            ).split(b"\0")
            if p
        ]
    )
    return {
        "contract": "cutback.committed-source-bytes.v1",
        "status": "passed"
        if not (different or missing or unsafe or staged or untracked)
        else "refused",
        "files": len(entries),
        "bytes": total,
        "different": different,
        "missing": missing,
        "unsafe": unsafe,
        "staged_changes": staged,
        "untracked": untracked,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = check(args.root)
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError):
        raise SystemExit("SOURCE_SNAPSHOT_CHECK_FAILED_DETAILS_SUPPRESSED") from None
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "passed" else 1)
