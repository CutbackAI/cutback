"""A fresh synthetic routing application with no private application dependency."""

import shutil
from pathlib import Path

from support_retrieval_application import git


def application(tmp_path):
    source = Path(__file__).resolve().parents[1] / "examples/source-routing"
    root = tmp_path / "independent-source-routing"
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("__pycache__"))
    git(root, "init")
    git(root, "config", "user.name", "Synthetic Cutback Test")
    git(root, "config", "user.email", "synthetic@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "Independent native routing application")
    return root
