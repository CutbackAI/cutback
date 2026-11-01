"""A fresh SDK/server-free wheel reuses a checked campaign receipt over real HTTP."""

import os
import subprocess
from pathlib import Path

import httpx
import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.core.workspace import write_private
from cutback.demo_workload_campaign import seed_workload_campaign
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_minimal_remote_campaign_finish_reuses_checks_without_sdk_or_credentials_in_files(
    api, tenant, tmp_path, monkeypatch
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
            "from cutback.agent_campaign_handoff import finish; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','fastapi','uvicorn','sqlalchemy','alembic','psycopg',"
            "'cryptography','jsonschema','stripe','boto3','jwt','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "CAMPAIGN_HANDOFF_REQUIRES_MATCHING_MINIMAL_WHEEL"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(
            base_url=endpoint, headers=api[1], trust_env=False, follow_redirects=False, timeout=30
        ) as client,
    ):
        result = seed_workload_campaign(
            api[2], client, tenant[0], tenant[1]["Authorization"][7:], tmp_path / "application-root"
        )
        root = Path(result["source_directory"])
        response = client.post(
            f"/v1/projects/{tenant[0]}/keys",
            json={"role": "project_admin", "environment": "staging"},
        )
        assert response.status_code == 201
        token = response.json()["token"]
        env["CUTBACK_CONTROL_TOKEN"] = token
        write_private(
            root / ".cutback/agent/connection.json", {"endpoint": endpoint, "project": tenant[0]}
        )
        command = [
            str(executable),
            "-I",
            "-m",
            "cutback.cli",
            "campaign",
            "finish",
            "--target",
            str(root),
            "--workspace",
            result["checked_handoff_workspace"],
            "--purpose-reference",
            "owned-minimal-receipt-reuse",
            "--sample-only",
            "--branch",
            "cutback/workload-campaign-review",
            "--execute-local",
        ]
        completed = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=90
        )
        assert token not in completed.stdout + completed.stderr
        assert completed.returncode == 0, "MINIMAL_CAMPAIGN_FINISH_FAILED"
        assert remote_json(completed)["status"] == "REVIEWED"
        current = client.get(f"/v1/projects/{tenant[0]}/campaigns/{result['campaign_id']}")
        assert current.status_code == 200
        assert current.json()["requests"] == result["report"]["requests"]
        for path in (root / ".cutback/agent").glob("*.json"):
            assert token not in path.read_text("utf-8")
        env["CUTBACK_CONTROL_TOKEN"] = tenant[2]["Authorization"][7:]
        denied = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=60
        )
        assert denied.returncode != 0
