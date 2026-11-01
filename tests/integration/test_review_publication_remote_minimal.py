"""Fresh HTTP-only CLI verifies a real confirmed application without running its code."""

import json
import os
import subprocess

import httpx
import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.core.workspace import write_private
from cutback.demo_campaign_outcomes import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_http_only_cli_previews_exact_review_with_current_scoped_authority(
    api, tenant, tmp_path, monkeypatch
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to a fresh core-plus-HTTPX wheel installation")
    executable = remote_interpreter(configured)
    env = remote_environment()
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util,sys; from pathlib import Path; import cutback; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert importlib.util.find_spec('httpx') is not None; "
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
    assert probe.returncode == 0, "PUBLICATION_AGENT_IS_NOT_AN_INDEPENDENT_MINIMAL_INSTALL"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(trust_env=False, follow_redirects=False, timeout=30) as client,
    ):
        result = run_sample(
            api[2],
            client,
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
        )
        applications = list(tmp_path.glob("outcome-*/application"))
        assert len(applications) == 1
        root = applications[0]
        key = client.post(
            endpoint + f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin", "environment": "staging"},
        )
        assert key.status_code == 201, "PUBLICATION_AGENT_SCOPED_KEY_CREATE_FAILED"
        token = key.json()["token"]
        env["CUTBACK_CONTROL_TOKEN"] = token
        write_private(
            root / ".cutback/agent/connection.json",
            {
                "endpoint": endpoint,
                "project": tenant[0],
            },
        )

        def invoke(*extra):
            response = subprocess.run(
                [
                    str(executable),
                    "-I",
                    "-m",
                    "cutback.cli",
                    "campaign",
                    "publish",
                    "--target",
                    str(root),
                    "--repository",
                    "example-owner/task-app",
                    "--base-branch",
                    "main",
                    "--head-branch",
                    "cutback/owned-outcome-review",
                    "--purpose-reference",
                    "owned-minimal-publication-review",
                    *extra,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            output = response.stdout + response.stderr
            if token in output or env["CUTBACK_CONTROL_TOKEN"] in output:
                pytest.fail("PUBLICATION_AGENT_DISCLOSED_CREDENTIAL", pytrace=False)
            return response

        response = invoke()
        assert response.returncode == 0, "MINIMAL_PUBLICATION_PREVIEW_FAILED"
        preview = remote_json(response)
        assert preview["intent_sha256"] == result["publication_preview"]["intent_sha256"]
        assert preview["proposed_commit"] == result["review"]["proposed_commit"]
        assert not preview["remote_published"] and preview["provider_calls"] == 0
        # Explicit publication cannot fall back to ambient provider/CLI credentials.
        response = invoke("--publish")
        assert response.returncode != 0
        assert "CUTBACK_GITHUB_TOKEN_REQUIRED_FOR_EXPLICIT_PUBLICATION" in response.stdout
        assert not (root / ".cutback/agent/campaign-publication.json").exists()
        # A metadata viewer cannot reuse the administrator's prior content review.
        env["CUTBACK_CONTROL_TOKEN"] = tenant[2]["Authorization"][7:]
        assert invoke().returncode != 0
        assert not (root / ".cutback/agent/campaign-publication.json").exists()
        current = client.get(
            endpoint + f"/v1/projects/{tenant[0]}/campaigns/{result['campaign_id']}",
            headers=api[1],
        )
        assert current.status_code == 200
        assert current.json()["requests"] == result["native_calls"]
        journal = json.dumps(json.loads((root / ".cutback/agent/campaign.json").read_text()))
        if token in journal or env["CUTBACK_CONTROL_TOKEN"] in journal:
            pytest.fail("PUBLICATION_AGENT_PERSISTED_CREDENTIAL", pytrace=False)
