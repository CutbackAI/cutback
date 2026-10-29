"""A fresh independent Git application, not an import of repository application code."""

import shutil
import subprocess
from pathlib import Path


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "core.autocrlf=false", "-C", str(root), *args],
        capture_output=True,
        check=True,
    ).stdout


def application(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples" / "retrieval-optimization"
    root = tmp_path / "independent-retrieval"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__"))
    git(root, "init")
    git(root, "config", "user.name", "Synthetic Cutback Test")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent synthetic RAG application")
    return root
