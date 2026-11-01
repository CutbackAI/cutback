"""The installed HTTP-only client discovers and freezes a cached current source without an SDK."""

import os
import subprocess

import pytest
from support_http import owned_collector
from support_policy_application import cached_application
from support_policy_cached_program import cached_program
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.policy_source import inspect_configuration
from cutback.scanner import scan

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("requested", [False, True])
def test_installed_core_only_current_reference_prepare_retry_inspect_and_cancel(
    api, tenant, tmp_path, monkeypatch, requested
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to a fresh matching core-plus-HTTPX wheel")
    executable, env = remote_interpreter(configured), remote_environment()
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util,sys; from pathlib import Path; import cutback; "
            "import cutback.agent_policy_program,cutback.agent_policy_activity; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(name) is None for name in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography',"
            "'jsonschema','sklearn','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "POLICY_PROGRAM_REQUIRES_MATCHING_MINIMAL_WHEEL"
    root, current = cached_application(tmp_path)
    _, binding = inspect_configuration(root, "app.py")
    site = next(row for row in scan(root)["call_sites"] if row["source_file"] == "app.py")
    base, _, dataset, _, executor, calls, _, _, request, _ = cached_program(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        requested=requested,
        source_binding=binding,
        cache_execution=current.cache_execution.model_dump(mode="json"),
        cache_controls=True,
        call_site_id=site["call_site_id"],
    )
    secret = api[1]["Authorization"][7:]
    env["CUTBACK_CONTROL_TOKEN"] = secret
    prefix = [str(executable), "-I", "-m", "cutback.cli", "source-policy"]

    def invoke(arguments):
        result = subprocess.run(
            prefix + arguments, cwd=root, env=env, capture_output=True, text=True, timeout=90
        )
        assert secret not in result.stdout + result.stderr
        assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in result.stdout + result.stderr
        assert result.returncode == 0, "MINIMAL_CACHED_CURRENT_ACTION_FAILED_CONTENT_OMITTED"
        return remote_json(result)

    try:
        with owned_collector(api[2]) as endpoint:
            scope = ["--target", str(root), "--endpoint", endpoint, "--project", tenant[0]]
            arguments = [
                "prepare",
                *scope,
                "--producer",
                "app.py",
                "--purpose-reference",
                "owned-minimal-current",
                "--max-usd",
                "100",
                "--max-requests",
                "2000",
                "--dataset",
                dataset["id"],
                "--baseline-snapshot",
                request["baseline_snapshot_id"],
                "--savings-per-success",
                "0.01",
                "--forecast-successful-tasks",
                "100000",
                "--controls",
                "1",
                "--no-generate",
                "--fixture-transport",
                "--minimum",
                "1",
                "--min-test-samples",
                "3",
                "--min-test-groups",
                "3",
            ]
            row = invoke(arguments)
            assert row["status"] == "READY_FOR_APPROVAL"
            assert row["economic_envelope"]["reference_runtime_cache_requested"] is requested
            assert invoke(arguments)["program_hash"] == row["program_hash"]
            observation = invoke(["inspect-activity", *scope, "--producer", "app.py"])
            assert observation["summary"]["scope"]["service_name"] == "owned-cached-current"
            assert observation["provider_calls"] == 0
            assert invoke(["status", "--target", str(root)])["id"] == row["id"]
            assert invoke(["cancel", "--target", str(root)])["status"] == "CANCELLED"
            assert (
                api[0]
                .get(base + "/source-policy-programs/" + row["id"], headers=api[1])
                .json()["requests"]
                == 0
            )
            assert calls == []
            for path in (root / ".cutback/agent").glob("*.json"):
                text = path.read_text("utf-8")
                assert secret not in text and "OWNED_PRIVATE_POLICY_INSTRUCTION" not in text
    finally:
        executor.close()
