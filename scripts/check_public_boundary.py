"""Check publication inputs without putting private marker values in source or logs."""

import argparse
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path

HOME_PATH = re.compile(
    rb"(?:[a-z]:[/\\]+users[/\\]+|/users/|/home/)[A-Za-z0-9][A-Za-z0-9_.-]*", re.I
)
PRIVATE_PATHS = {".env", ".cutback", ".venv", ".terraform", "node_modules", "secrets", "artifacts"}


def markers_from(path, root):
    if path is None:
        return ()
    path = path.resolve(strict=True)
    if path.is_relative_to(root):
        raise ValueError("Private publication policy must stay outside the repository")
    values = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or len(value) < 4 for value in values)
    ):
        raise ValueError("Private publication policy must be a nonempty list of marker strings")
    return tuple(value.casefold().encode() for value in values)


def private_content(data, markers):
    lower = data.lower()
    return bool(HOME_PATH.search(data)) or any(value in lower for value in markers)


def private_path(name):
    parts = Path(name).parts
    return any(part in PRIVATE_PATHS for part in parts) or (
        "MASTER_ENGINEERING_HANDOFF" in name or "private-notes" in name
    )


def check_files(root, markers):
    paths = set(
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
        ).split(b"\0")
    )
    failures, checked = 0, 0
    for raw in paths:
        if not raw:
            continue
        name = raw.decode("utf-8")
        path = root / name
        if not path.exists():
            continue
        checked += 1
        if private_path(name) or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            failures += 1
        elif private_content(raw + b"\n" + path.read_bytes(), markers):
            failures += 1
    return checked, failures


def check_history(root, markers):
    ids = subprocess.check_output(["git", "rev-list", "--objects", "--all"], cwd=root)
    names = [line.split(b" ", 1)[0] for line in ids.splitlines()]
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=b"\n".join(names) + b"\n",
        capture_output=True,
        check=True,
    )
    stream = io.BytesIO(result.stdout)
    checked, failures = 0, 0
    while header := stream.readline():
        _, kind, size = header.split()
        data = stream.read(int(size))
        assert stream.read(1) == b"\n"
        if kind in {b"blob", b"tree", b"commit", b"tag"}:
            checked += 1
            failures += private_content(data, markers)
    return checked, failures


def check_archive(path, markers):
    checked, failures = 0, 0
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            entries = [
                (name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")
            ]
    else:
        with tarfile.open(path) as archive:
            entries = [
                (member.name, archive.extractfile(member).read())
                for member in archive
                if member.isfile()
            ]
    for name, data in entries:
        checked += 1
        if private_path(name) or private_content(name.encode() + b"\n" + data, markers):
            failures += 1
    return checked, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--markers-file", type=Path)
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--archive", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    try:
        markers = markers_from(args.markers_file, root)
        files, failures = check_files(root, markers)
        objects = 0
        if args.history:
            objects, count = check_history(root, markers)
            failures += count
        members = 0
        for archive in args.archive:
            count, failed = check_archive(archive, markers)
            members += count
            failures += failed
        print(
            json.dumps(
                {
                    "files_checked": files,
                    "history_objects_checked": objects,
                    "archive_members_checked": members,
                    "findings": failures,
                    "private_marker_values_logged": False,
                }
            )
        )
        return 1 if failures else 0
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ):
        print("Publication boundary could not be verified; inspect inputs privately.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
