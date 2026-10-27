"""Install the built wheel with only core dependencies, then scan without optional SDKs."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directory = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "dist"
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
(wheel,) = directory.glob(f"cutback-{version}-*.whl")
with tempfile.TemporaryDirectory(prefix="cutback-wheel-") as temporary:
    environment = Path(temporary) / "venv"
    subprocess.run(
        [os.getenv("CUTBACK_UV", "uv"), "venv", str(environment), "--python", "3.11.18"], check=True
    )
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [os.getenv("CUTBACK_UV", "uv"), "pip", "install", "--python", str(executable), str(wheel)],
        check=True,
    )
    probe = subprocess.run(
        [
            str(executable),
            "-c",
            "import importlib.util; from cutback.pricing import Catalog; "
            f"import cutback; assert cutback.__version__=={version!r}; "
            'assert importlib.util.find_spec("openai") is None; '
            'assert importlib.util.find_spec("fastapi") is None; print(Catalog().snapshot_id)',
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=temporary,
    )
    consumer = Path(temporary) / "independent-application"
    consumer.mkdir()
    source = consumer / "main.py"
    original = (
        '"""Owned synthetic source fixture; scanning must never execute this."""\n'
        'raise RuntimeError("source must not execute")\n'
        "from openai import OpenAI\n"
        "client = OpenAI()\n"
        "def classify(text):\n"
        '    return client.responses.create(model="gpt-4.1", input=text, max_output_tokens=64)\n'
    )
    source.write_text(original, encoding="utf-8")
    private_sentinel = "synthetic-private-environment-value"
    (consumer / ".env").write_text("PRIVATE_VALUE=" + private_sentinel, encoding="utf-8")
    (consumer / ".gitignore").write_text(".env\n.cutback/\n", encoding="utf-8")

    def command(*args):
        process = subprocess.run(
            [str(executable), "-m", "cutback.cli", *args],
            capture_output=True,
            text=True,
            check=True,
            cwd=consumer,
            env={**os.environ, "CUTBACK_ENABLED": "false"},
        )
        assert private_sentinel not in process.stdout + process.stderr
        return json.loads(process.stdout)

    assert command("doctor", "--json")["network_probe"] == "not_performed"
    assert command("init", "--mode", "local")["telemetry"] == "disabled"
    subprocess.run(
        [
            str(executable),
            "-m",
            "cutback.cli",
            "scan",
            ".",
            "--json",
            "--output",
            ".cutback/scan.json",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=consumer,
    )
    result = json.loads((consumer / ".cutback/scan.json").read_text(encoding="utf-8"))
    assert result["summary"]["llm_call_sites_detected"] == 1
    assert result["summary"]["actual_spend_usd"] is None
    assert private_sentinel not in json.dumps(result)
    opportunities = command("opportunities", "--target", ".", "--json")
    assert opportunities["expected_savings_usd"] is None and opportunities["opportunities"]
    plan = command("instrument", "--target", ".", "--entrypoint", "main.py", "--plan", "--json")
    assert plan["status"] == "plan_only" and plan["content_capture"] is False
    assert "# cutback:bootstrap" in plan["diff"] and source.read_text(encoding="utf-8") == original
    assert command("datasets", "discover", "--target", ".")  # Parse-only discovery needs no server.
    job = command("optimize", ".", "--json")
    assert job["phase"] == "ANALYZED" and job["profiles"]
    assert job["next_action"]["provider_calls"] == 0
    assert command("workflow", "status")["id"] == job["id"]
    subprocess.run(
        [str(executable), "-m", "cutback.cli", "sample", str(Path(temporary) / "support-desk")],
        check=True,
        capture_output=True,
        text=True,
        cwd=consumer,
    )
    sample_source = Path(temporary) / "support-desk" / "app.py"
    assert "from openai import OpenAI" in sample_source.read_text()
    assert "import cutback" not in sample_source.read_text()
    print(
        "Core-only wheel and independent consumer doctor/scan/opportunities/instrument plan passed."
    )
