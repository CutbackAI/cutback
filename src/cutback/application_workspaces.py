"""Materialize checked source trees without copying repository history or running hooks.

This is local source preparation, not evaluation authority or a sandbox. Only
tracked ordinary files and the exact base commit/tree are exported. Application
checks remain a separate explicit operation through the ordinary check runner.
"""

import hashlib
import ntpath
import os
import re
import time
import zlib
from pathlib import Path, PureWindowsPath

from cutback.application_checks import SourceTree, git, snapshot, source_root
from cutback.core.request_profiles import safe_relative
from cutback.core.workspace import prepare_private_output, write_private

CONTRACT = "cutback.application-workspaces.v1"
MAX_FILE = 16777216
MAX_TOTAL = 67108864


def windows_reserved(part: str):
    check = getattr(ntpath, "isreserved", None)
    return bool(check(part)) if callable(check) else PureWindowsPath(part).is_reserved()


def object_identity(kind: str, value: bytes, algorithm: str):
    raw = kind.encode() + b" " + str(len(value)).encode() + b"\0" + value
    return hashlib.new(algorithm, raw).hexdigest(), raw


def write_new(path: Path, raw: bytes, *, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(raw)
    if executable and os.name != "nt":
        path.chmod(0o700)


def store_object(root: Path, kind: str, value: bytes, algorithm: str):
    identifier, raw = object_identity(kind, value, algorithm)
    path = root / ".git" / "objects" / identifier[:2] / identifier[2:]
    if path.exists():
        if path.is_symlink() or zlib.decompress(path.read_bytes()) != raw:
            raise ValueError("APPLICATION_WORKSPACE_OBJECT_COLLISION")
    else:
        write_new(path, zlib.compress(raw))
    return identifier


def tree_object(root: Path, entries, algorithm: str):
    tree: dict = {}
    for path, mode, identifier in entries:
        parent = tree
        parts = path.split("/")
        for name in parts[:-1]:
            value = parent.setdefault(name, {})
            if not isinstance(value, dict):
                raise ValueError("APPLICATION_WORKSPACE_PATH_COLLISION")
            parent = value
        if parts[-1] in parent:
            raise ValueError("APPLICATION_WORKSPACE_PATH_COLLISION")
        parent[parts[-1]] = (mode, identifier)

    def encode(values):
        body = []
        for name, item in sorted(
            values.items(),
            key=lambda pair: pair[0].encode() + (b"/" if isinstance(pair[1], dict) else b""),
        ):
            if isinstance(item, dict):
                mode, identifier = "40000", encode(item)
            else:
                mode, identifier = item
            body.append(mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(identifier))
        return store_object(root, "tree", b"".join(body), algorithm)

    return encode(tree)


def source_objects(root: Path, frozen: SourceTree, replacements: dict[str, bytes]):
    """Read bounded exact blobs, never pack/clone/fetch or repository clean filters."""
    if not isinstance(replacements, dict) or not 1 <= len(replacements) <= 8:
        raise ValueError("APPLICATION_WORKSPACE_REPLACEMENT_BOUND_REQUIRED")
    if any(
        name not in frozen.files or type(raw) is not bytes or len(raw) > MAX_FILE
        for name, raw in replacements.items()
    ):
        raise ValueError("APPLICATION_WORKSPACE_TRACKED_REPLACEMENTS_REQUIRED")
    algorithm = "sha1" if len(frozen.commit) == 40 else "sha256"
    commit = git(root, "cat-file", "commit", frozen.commit)
    if object_identity("commit", commit, algorithm)[0] != frozen.commit:
        raise ValueError("APPLICATION_WORKSPACE_BASE_COMMIT_CHANGED")
    match = re.match(rb"tree ([a-f0-9]{40,64})\n", commit)
    if not match:
        raise ValueError("APPLICATION_WORKSPACE_BASE_TREE_INVALID")
    rows: list[tuple[str, str, str, bytes, bytes, bytes]] = []
    total = 0
    portable_names: dict[str, str] = {}
    for row in git(root, "ls-tree", "-r", "-z", frozen.commit).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        mode, kind, identifier = header.decode("ascii").split()
        path = name.decode("utf-8")
        safe_relative(path)
        if kind != "blob" or mode not in {"100644", "100755"} or path not in frozen.files:
            raise ValueError("APPLICATION_WORKSPACE_ORDINARY_TRACKED_FILES_REQUIRED")
        prefixes = path.split("/")
        if len(prefixes) > 32:
            raise ValueError("APPLICATION_WORKSPACE_PATH_DEPTH_EXCEEDED")
        for count in range(1, len(prefixes) + 1):
            prefix = "/".join(prefixes[:count])
            part = prefixes[count - 1]
            lower = part.casefold()
            if lower in {".cutback", ".git-credentials", ".netrc"} or (
                (lower == ".env" or lower.startswith(".env."))
                and not lower.endswith((".example", ".sample", ".template"))
            ):
                raise ValueError(
                    "APPLICATION_WORKSPACE_TRACKED_CREDENTIAL_FILES_REQUIRE_SOURCE_REVIEW"
                )
            if (
                lower in {".git", ".git~1", "git~1"}
                or part.rstrip(" .") != part
                or windows_reserved(part)
                or portable_names.setdefault(prefix.casefold(), prefix) != prefix
            ):
                raise ValueError("APPLICATION_WORKSPACE_PORTABLE_PATH_REQUIRED")
        file = root / path
        if (
            file.is_symlink()
            or not file.is_file()
            or file.stat().st_size > MAX_FILE
            or any(
                item.is_symlink() or getattr(item.lstat(), "st_file_attributes", 0) & 1024
                for item in (file, *file.parents)
                if item.is_relative_to(root)
            )
        ):
            raise ValueError("APPLICATION_WORKSPACE_SOURCE_FILE_CHANGED")
        with file.open("rb") as stream:
            actual = stream.read(MAX_FILE + 1)
        if len(actual) > MAX_FILE:
            raise ValueError("APPLICATION_WORKSPACE_SOURCE_BOUND_EXCEEDED")
        if hashlib.sha256(actual).hexdigest() != frozen.files[path]:
            raise ValueError("APPLICATION_WORKSPACE_SOURCE_FILE_CHANGED")
        canonical = actual
        if object_identity("blob", canonical, algorithm)[0] != identifier:
            canonical = actual.replace(b"\r\n", b"\n")
            if object_identity("blob", canonical, algorithm)[0] != identifier:
                raise ValueError("APPLICATION_WORKSPACE_SOURCE_BLOB_CHANGED")
        proposed = replacements.get(path, actual)
        if path in replacements and proposed == actual:
            raise ValueError("APPLICATION_WORKSPACE_EMPTY_REPLACEMENT")
        total += max(len(actual), len(proposed))
        if total > MAX_TOTAL or len(rows) >= 10000:
            raise ValueError("APPLICATION_WORKSPACE_SOURCE_BOUND_EXCEEDED")
        rows.append((path, mode, identifier, actual, canonical, proposed))
    if {row[0] for row in rows} != set(frozen.files) or snapshot(root) != frozen:
        raise ValueError("APPLICATION_WORKSPACE_SOURCE_CHANGED_DURING_READ")
    return algorithm, commit, match[1].decode(), rows


def materialize(
    root: Path, destination: Path, expected: SourceTree, replacements: dict[str, bytes]
):
    """Prepare two independent clean trees; never execute checks or edit original source.

    The caller must refresh its canonical source proposal and supply the exact
    replacement bytes. This facility alone grants no improvement/publication right.
    An existing destination is always refused, including after interruption.
    """
    prepare_private_output(Path(os.path.abspath(root)) / ".cutback-workspace-path-check")
    root, expected = source_root(root), SourceTree.model_validate(expected)
    if snapshot(root) != expected:
        raise ValueError("APPLICATION_WORKSPACE_EXPECTED_SOURCE_CHANGED")
    destination = Path(os.path.abspath(destination))
    resolved = destination.resolve()
    if resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError("APPLICATION_WORKSPACE_DESTINATION_MUST_BE_INDEPENDENT")
    if destination.exists() or destination.is_symlink():
        raise ValueError("APPLICATION_WORKSPACE_DESTINATION_ALREADY_EXISTS")
    prepare_private_output(destination.parent / ".cutback-workspace-path-check")
    algorithm, commit, tree_id, rows = source_objects(root, expected, replacements)
    destination.mkdir(mode=0o700)
    manifest = {
        "contract": CONTRACT,
        "status": "PREPARING",
        "source_commit": expected.commit,
        "source_tree_hash": expected.tree_hash,
        "ancestor_history_copied": False,
        "ignored_files_copied": False,
        "application_checks_executed": False,
        "production_deployed": False,
    }
    write_private(destination / "workspaces.json", manifest, exclusive=True)
    try:
        states = {}
        for phase in ("base", "proposed"):
            target = destination / phase
            target.mkdir(mode=0o700)
            git(target, "init", "--template=", "--object-format=" + algorithm)
            original_entries, changed_entries = [], []
            for path, mode, identifier, actual, canonical, proposed in rows:
                if store_object(target, "blob", canonical, algorithm) != identifier:
                    raise ValueError("APPLICATION_WORKSPACE_SOURCE_OBJECT_CHANGED")
                original_entries.append((path, mode, identifier))
                replacement = (
                    store_object(target, "blob", proposed, algorithm)
                    if phase == "proposed" and path in replacements
                    else identifier
                )
                changed_entries.append((path, mode, replacement))
                write_new(
                    target / path,
                    actual if phase == "base" else proposed,
                    executable=mode == "100755",
                )
            if tree_object(target, original_entries, algorithm) != tree_id:
                raise ValueError("APPLICATION_WORKSPACE_BASE_TREE_CHANGED")
            if store_object(target, "commit", commit, algorithm) != expected.commit:
                raise ValueError("APPLICATION_WORKSPACE_BASE_COMMIT_CHANGED")
            write_new(target / ".git/shallow", expected.commit.encode() + b"\n")
            head = expected.commit
            if phase == "proposed":
                changed_tree = tree_object(target, changed_entries, algorithm)
                stamp = str(int(time.time())).encode() + b" +0000"
                identity = b"Cutback Review <cutback@localhost> " + stamp
                raw = (
                    b"tree "
                    + changed_tree.encode()
                    + b"\nparent "
                    + expected.commit.encode()
                    + b"\nauthor "
                    + identity
                    + b"\ncommitter "
                    + identity
                    + b"\n\nPrepare checked Cutback source change\n"
                )
                head = store_object(target, "commit", raw, algorithm)
            (target / ".git/HEAD").write_text(head + "\n", encoding="ascii")
            git(target, "read-tree", "HEAD")
            states[phase] = snapshot(target)
        if states["base"] != expected or snapshot(root) != expected:
            raise ValueError("APPLICATION_WORKSPACE_ORIGINAL_SOURCE_CHANGED")
        changed = {
            name
            for name in expected.files
            if states["base"].files[name] != states["proposed"].files[name]
        }
        if changed != set(replacements) or any(
            states["proposed"].files[name] != hashlib.sha256(raw).hexdigest()
            for name, raw in replacements.items()
        ):
            raise ValueError("APPLICATION_WORKSPACE_PROPOSED_BYTES_CHANGED")
        manifest.update(
            status="WORKSPACES_READY",
            base_root=str(destination / "base"),
            proposed_root=str(destination / "proposed"),
            proposed_commit=states["proposed"].commit,
            proposed_tree_hash=states["proposed"].tree_hash,
            replacement_sha256={
                name: hashlib.sha256(raw).hexdigest() for name, raw in replacements.items()
            },
        )
        write_private(destination / "workspaces.json", manifest)
        return manifest
    except Exception:
        write_private(destination / "workspaces.json", {**manifest, "status": "PREPARATION_FAILED"})
        raise
