"""Audit the pinned CPU lock against upstream release advisories without skipped versions."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="cutback-training-audit-") as temporary:
    requirements = Path(temporary) / "training.txt"
    subprocess.run(
        [
            os.getenv("CUTBACK_UV", "uv"),
            "export",
            "--project",
            str(root / "infra/training"),
            "--python",
            "3.11.18",
            "--locked",
            "--no-dev",
            "--format",
            "requirements-txt",
            "--output-file",
            str(requirements),
            "--quiet",
        ],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    content = requirements.read_text(encoding="utf-8")
    content, count = re.subn(r"(?m)^torch==(\d+\.\d+\.\d+)\+cpu", r"torch==\1", content)
    if count != 1 or re.search(r"(?m)^\S+==\S*\+", content):
        raise SystemExit("Review the native training release/advisory mapping before auditing.")
    requirements.write_text(content, encoding="utf-8")
    print(
        "CPU artifact hashes remain locked; "
        "advisory lookup maps torch +cpu to its upstream release.",
        flush=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            str(requirements),
            "--no-deps",
            "--disable-pip",
            "--strict",
        ],
        check=True,
    )
