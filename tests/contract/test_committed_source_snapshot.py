"""Publication checks raw committed bytes, including drift hidden by text filters."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_source_snapshot.py"
spec = importlib.util.spec_from_file_location("source_snapshot_guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, check=True
    ).stdout


def repository(path):
    path.mkdir()
    git(path, "init")
    git(path, "config", "core.autocrlf", "false")
    (path / ".gitattributes").write_bytes(b"* text=auto\n")
    (path / ".gitignore").write_bytes(b"artifacts/\n")
    (path / "application.py").write_bytes(b"value = 'owned synthetic task'\n")
    git(path, "add", ".")
    git(
        path,
        "-c",
        "user.name=Owned fixture",
        "-c",
        "user.email=synthetic@example.invalid",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-m",
        "Owned synthetic snapshot",
    )
    return path


def test_clean_snapshot_and_ignored_artifacts_pass_without_application_execution(tmp_path):
    root = repository(tmp_path / "source")
    (root / "artifacts").mkdir()
    (root / "artifacts/owned-output.txt").write_text("not a publication input")
    result = guard.check(root)
    assert result["status"] == "passed" and result["files"] == 3
    assert result["different"] == result["untracked"] == 0


def test_raw_crlf_drift_hidden_by_git_status_is_refused(tmp_path):
    root = repository(tmp_path / "source")
    path = root / "application.py"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert git(root, "diff", "--name-only") == b""
    result = guard.check(root)
    assert result["status"] == "refused" and result["different"] == 1
    assert "application.py" not in json.dumps(result)


def test_staged_and_untracked_inputs_cannot_be_claimed_as_a_frozen_snapshot(tmp_path):
    root = repository(tmp_path / "source")
    (root / "application.py").write_text(
        "value = 'changed synthetic task'\n", encoding="utf-8", newline="\n"
    )
    git(root, "add", "application.py")
    (root / "opaque-owned-note.txt").write_text("owned unpublished input")
    result = guard.check(root)
    assert result["status"] == "refused" and result["staged_changes"] and result["untracked"] == 1
    assert "opaque-owned-note" not in json.dumps(result)


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink permission is separately unavailable")
def test_worktree_symlink_never_reads_an_outside_file(tmp_path):
    root = repository(tmp_path / "source")
    outside = tmp_path / "owned-outside.txt"
    outside.write_text("not a release input")
    path = root / "application.py"
    path.unlink()
    path.symlink_to(outside)
    result = guard.check(root)
    assert result["status"] == "refused" and result["unsafe"] == 1


def test_ambient_git_directory_cannot_redirect_the_explicit_snapshot_root(tmp_path, monkeypatch):
    intended = repository(tmp_path / "intended")
    other = repository(tmp_path / "other")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert guard.check(intended)["status"] == "passed"
