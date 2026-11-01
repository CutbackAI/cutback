"""A core-plus-HTTPX installed wheel inspects real scoped HTTP without any SDK."""

import os
import subprocess

import pytest
from support_http import owned_collector
from support_policy_application import application
from support_remote_agent import remote_environment, remote_interpreter, remote_json

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("asynchronous", [False, True])
def test_core_only_installed_inspection_uses_viewer_key_without_application_imports(
    api, tenant, tmp_path, asynchronous
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
            "import importlib.util,sys; from pathlib import Path; "
            "import cutback,cutback.agent_policy_activity; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(name) is None for name in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "POLICY_ACTIVITY_REQUIRES_MATCHING_MINIMAL_WHEEL"
    root = application(tmp_path, async_consumer=asynchronous)
    secret = tenant[2]["Authorization"][7:]
    env["CUTBACK_CONTROL_TOKEN"] = secret
    with owned_collector(api[2]) as endpoint:
        result = subprocess.run(
            [
                str(executable),
                "-I",
                "-m",
                "cutback.cli",
                "source-policy",
                "inspect-activity",
                "--target",
                str(root),
                "--producer",
                "app.py",
                "--service",
                "owned-core-only",
                "--endpoint",
                endpoint,
                "--project",
                tenant[0],
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
    assert secret not in result.stdout + result.stderr
    assert "OWNED_PRIVATE_POLICY_INSTRUCTION" not in result.stdout + result.stderr
    assert result.returncode == 0, "POLICY_ACTIVITY_REMOTE_INSPECTION_FAILED_DETAILS_OMITTED"
    receipt = remote_json(result)
    assert receipt["status"] == "RETAINED_METADATA_ONLY" and receipt["provider_calls"] == 0
    assert receipt["summary"]["project_id"] == tenant[0]
    assert receipt["summary"]["retained_operations"] == 0
    assert (
        receipt["summary"]["native_sdk_invocations"]
        is receipt["summary"]["cache_component_upper_usd"]
        is None
    )
