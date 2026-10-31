"""Install the built wheel with only core dependencies, then scan without optional SDKs."""

import json
import os
import re
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
            "from cutback.core.cache_workload import CacheWorkloadAssessment; "
            "from cutback.core.retrieval_producer import RetrievalProducerBinding; "
            "from cutback.retrieval_source import inspect_producer; "
            "from cutback.agent_cache_workload import source_input; "
            "from cutback.workflow_operator import run_workflow; "
            f"import cutback; assert cutback.__version__=={version!r}; "
            "from cutback.gateway.client import operation_headers; from uuid import UUID; "
            "from cutback.core.retrieval import "
            "RetrievalCorpus, RetrievalSettings, LocalRetrievalIndex; "
            "corpus=RetrievalCorpus.model_validate({'documents':[{'document_id':str(UUID(int=1)),"
            "'text':'synthetic refund deadline fourteen-days'}]}); "
            "result=LocalRetrievalIndex(corpus,RetrievalSettings()).query('refund'); "
            "assert len(result.passages)==1; "
            "assert UUID(operation_headers()['X-Cutback-Operation-ID']).int != 0; "
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
    guide = command("workflow", "explain", "--json")
    assert guide["qualified_request_profiles"] == 1 and guide["provider_calls"] == 0
    assert guide["source_changed"] is None and guide["source_edit_performed"] is False
    blocked = Path(temporary) / "unsupported-request"
    blocked.mkdir()
    blocked_source = (
        "from openai import OpenAI\nclient=OpenAI()\ndef classify(text):\n"
        "    return client.responses.create(model='gpt-4.1', input=text, **options)\n"
        "raise RuntimeError('must-not-execute')\n"
    )
    (blocked / "app.py").write_text(blocked_source, encoding="utf-8")
    unsupported = command("optimize", str(blocked), "--json")
    assert not unsupported["profiles"]
    explanation = command("workflow", "explain", "--target", str(blocked), "--json")
    assert explanation["issues"][0]["diagnostic_code"] == (
        "EXPANDED_REQUEST_ARGUMENTS_REQUIRE_REVIEW"
    )
    assert explanation["next_action"]["tool"] == "cutback workflow explain"
    assert (blocked / "app.py").read_text(encoding="utf-8") == blocked_source
    git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    git_env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1")
    for arguments in (
        ["init", "--quiet"],
        ["add", "main.py", ".gitignore"],
        [
            "-c",
            "user.name=Owned Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgSign=false",
            "commit",
            "--quiet",
            "-m",
            "Synthetic source",
        ],
    ):
        subprocess.run(
            ["git", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *arguments],
            cwd=consumer,
            env=git_env,
            check=True,
            capture_output=True,
        )
    attribution = command("workflow", "refresh-source-map")
    assert attribution["provider_calls"] == 0 and attribution["mapped_files"] == 1
    assert attribution["application_source_changed"] is False
    assert source.read_text(encoding="utf-8") == original
    assert private_sentinel not in (consumer / attribution["source_map_path"]).read_text()
    help_result = subprocess.run(
        [str(executable), "-m", "cutback.cli", "qualify", "load", "--help"],
        capture_output=True,
        text=True,
        cwd=consumer,
        check=True,
    )
    # CI can force Rich's ANSI styling even with captured stdout. Options may
    # contain separate escape-delimited dash/name spans; inspect visible text.
    visible_help = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", help_result.stdout)
    assert "--execute-local" in visible_help
    stage_help = subprocess.run(
        [str(executable), "-m", "cutback.cli", "workflow", "run", "--help"],
        capture_output=True,
        text=True,
        cwd=consumer,
        check=True,
    )
    visible_stage_help = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", stage_help.stdout)
    assert "--local-worker" in visible_stage_help and "--max-wait-seconds" in visible_stage_help
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
    # Installed core alone discovers and executes an independent application's
    # own guarded check. No SDK/server/evaluator dependency is installed here.
    check_root = Path(temporary) / "application-checks"
    subprocess.run(
        [str(executable), str(root / "examples/application-checks/sample.py"), str(check_root)],
        check=True,
        capture_output=True,
        text=True,
        cwd=temporary,
    )
    contract_file, discovery_file = check_root / "contract.json", check_root / "discovery.json"
    for arguments in (
        [
            "checks",
            "discover",
            str(check_root / "base"),
            str(check_root / "proposed"),
            "--output",
            str(contract_file),
            "--report",
            str(discovery_file),
        ],
        [
            "checks",
            "run",
            str(contract_file),
            str(check_root / "base"),
            str(check_root / "proposed"),
            "--execute-local",
            "--output",
            str(check_root / "receipt.json"),
            "--junit",
            str(check_root / "junit.xml"),
        ],
    ):
        subprocess.run(
            [str(executable), "-m", "cutback.cli", *arguments],
            check=True,
            capture_output=True,
            text=True,
            cwd=temporary,
        )
    assert json.loads((check_root / "receipt.json").read_text(encoding="utf-8"))["all_passed"]
    assert (
        json.loads(discovery_file.read_text(encoding="utf-8"))["application_commands_executed"] == 0
    )
    modern = consumer / "modern.py"
    modern.write_text(original.replace('model="gpt-4.1"', 'model="gpt-6-luna"'), encoding="utf-8")
    subprocess.run(
        [
            str(executable),
            "-c",
            "import sys; from pathlib import Path; from cutback.scanner import scan; "
            "from cutback.core.request_profiles import prepare_profile; "
            "root=Path('.'); inventory=scan(root); "
            "site=next(row for row in inventory['call_sites'] "
            "if row['requested_model']=='gpt-6-luna'); "
            "baseline=prepare_profile(root,site).baseline(); "
            "assert baseline['native_contract']['reasoning_effort']=='medium'; "
            "assert baseline['native_contract']['service_tier']=='auto'; "
            "assert not any(name in sys.modules for name in ('openai','fastapi','sqlalchemy')); "
            "print('Modern source preparation stayed core-only and parse-only')",
        ],
        check=True,
        capture_output=True,
        text=True,
        cwd=consumer,
    )
    print("Core-only wheel, source/native preparation and discovered application checks passed.")
