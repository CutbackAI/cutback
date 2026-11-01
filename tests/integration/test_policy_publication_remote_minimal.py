"""HTTP-only policy publisher refuses real synthetic/unauthorized collector reviews."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter

from cutback.demo_policy_program import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_minimal_policy_publisher_refreshes_actual_authority_without_sdk_or_external_network(
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
            "import cutback.agent_policy_publication,cutback.policy_publication; "
            "assert Path(cutback.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "assert all(importlib.util.find_spec(n) is None for n in "
            "('openai','anthropic','fastapi','sqlalchemy','alembic','psycopg','cryptography','pytest'))",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "MATCHING_MINIMAL_POLICY_PUBLICATION_INSTALL_REQUIRED"
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
        root = Path(result["source_directory"])
        key = client.post(
            endpoint + f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin", "environment": "staging"},
        )
        assert key.status_code == 201
        token = key.json()["token"]
        env["CUTBACK_CONTROL_TOKEN"] = token
        env["CUTBACK_GITHUB_TOKEN"] = "owned-synthetic-no-network-token"
        # Even a bug in the local gate cannot send a request outside this owned
        # collector. The child has no vendor SDK or server installation.
        launch = (
            "import httpx,runpy,sys\n"
            "original=httpx.Client.send\n"
            "def owned(self,request,*args,**kwargs):\n"
            " assert request.url.host=='127.0.0.1' and request.url.port=="
            + str(urlsplit(endpoint).port)
            + ", 'EXTERNAL_NETWORK_FORBIDDEN_IN_OWNED_TEST'\n"
            " return original(self,request,*args,**kwargs)\n"
            "httpx.Client.send=owned\n"
            "sys.argv=['cutback','source-policy','publish',*sys.argv[1:]]\n"
            "runpy.run_module('cutback.cli',run_name='__main__')\n"
        )

        def invoke():
            response = subprocess.run(
                [
                    str(executable),
                    "-I",
                    "-c",
                    launch,
                    "--target",
                    str(root),
                    "--repository",
                    "example-owner/policy-app",
                    "--head-branch",
                    "cutback/checked-policy",
                    "--purpose-reference",
                    "owned-minimal-policy-review",
                    "--publish",
                ],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            assert token not in response.stdout + response.stderr
            assert env["CUTBACK_GITHUB_TOKEN"] not in response.stdout + response.stderr
            return response

        denied = invoke()
        assert denied.returncode != 0, "SYNTHETIC_POLICY_PUBLICATION_MUST_BE_REFUSED"
        assert "REVIEW_PUBLICATION_POLICY_HELDOUT_REVIEW_REQUIRED" in denied.stdout
        assert not (root / ".cutback/agent/policy-program-publication.json").exists()
        env["CUTBACK_CONTROL_TOKEN"] = tenant[2]["Authorization"][7:]
        assert invoke().returncode != 0
        assert not (root / ".cutback/agent/policy-program-publication.json").exists()
        final = client.get(
            endpoint + f"/v1/projects/{tenant[0]}/source-policy-programs/{result['program_id']}",
            headers=api[1],
        )
        assert final.status_code == 200 and final.json()["requests"] == result["native_sdk_calls"]
        for path in (root / ".cutback/agent").glob("*.json"):
            saved = path.read_text("utf-8")
            assert token not in saved and env["CUTBACK_GITHUB_TOKEN"] not in saved
