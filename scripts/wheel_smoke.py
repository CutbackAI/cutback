"""Install the built wheel with only core dependencies, then scan without optional SDKs."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
wheel = sorted((root / "dist").glob("*.whl"))[-1]
with tempfile.TemporaryDirectory(prefix="cutback-wheel-") as temporary:
    environment = Path(temporary) / "venv"
    subprocess.run(["uv", "venv", str(environment), "--python", "3.11"], check=True)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(["uv", "pip", "install", "--python", str(executable), str(wheel)], check=True)
    probe = subprocess.run(
        [
            str(executable),
            "-c",
            "import importlib.util; from cutback.pricing import Catalog; "
            'assert importlib.util.find_spec("openai") is None; '
            'assert importlib.util.find_spec("fastapi") is None; print(Catalog().snapshot_id)',
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=temporary,
    )
    cli = subprocess.run(
        [
            str(executable),
            "-m",
            "cutback.cli",
            "scan",
            str(root / "examples/minimal-openai"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=temporary,
    )
    result = json.loads(cli.stdout)
    assert result["summary"]["llm_call_sites_detected"] == 1
    assert result["summary"]["actual_spend_usd"] is None
    print("Core-only wheel install, bundled pricing, CLI and offline scan passed.")
