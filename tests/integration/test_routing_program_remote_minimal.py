"""A fresh installed HTTP-only agent observes and reviews the actual native program."""

import os
import subprocess
from pathlib import Path

import httpx
import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.core.workspace import write_private
from cutback.demo_routing_program import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_installed_http_only_program_agent_has_no_provider_or_server_dependencies(
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
            "from cutback.agent_routing_program import run; "
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
    assert probe.returncode == 0, "ROUTING_PROGRAM_AGENT_REQUIRES_INDEPENDENT_MINIMAL_INSTALL"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(
            base_url=endpoint, trust_env=False, follow_redirects=False, timeout=30
        ) as client,
    ):
        receipt = run_sample(
            api[2],
            client,
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
            simple_controls_pass=True,
        )
        workspace = Path(receipt["workspace"])
        root = workspace / "application"
        response = client.post(
            f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin", "environment": "staging"},
        )
        assert response.status_code == 201
        token = response.json()["token"]
        env["CUTBACK_CONTROL_TOKEN"] = token
        write_private(
            root / ".cutback/agent/connection.json", {"endpoint": endpoint, "project": tenant[0]}
        )

        def invoke(command, *extra):
            response = subprocess.run(
                [
                    str(executable),
                    "-I",
                    "-m",
                    "cutback.cli",
                    "routing",
                    "program",
                    command,
                    "--target",
                    str(root),
                    *extra,
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert token not in response.stdout + response.stderr, (
                "PROGRAM_AGENT_DISCLOSED_CREDENTIAL"
            )
            return response, remote_json(response)

        response, metadata = invoke("status")
        assert response.returncode == 0 and metadata["id"] == receipt["program_id"]
        assert (
            metadata["status"] == "COMPLETED"
            and metadata["requests"] == receipt["native_sdk_calls"]
        )
        response, observed = invoke("run", "--max-polls", "1", "--max-wait-seconds", "10")
        assert response.returncode == 0 and observed["requests"] == receipt["native_sdk_calls"]
        response, proposed = invoke(
            "proposal",
            "--purpose-reference",
            "owned-minimal-source-proposal",
            "--output",
            str(tmp_path / "remote-proposal.json"),
        )
        assert response.returncode == 0 and proposed["status"] == "PROPOSAL_READY"
        response, reviewed = invoke(
            "review",
            str(workspace / "contract.json"),
            str(workspace / "checks.json"),
            receipt["proposed_application"],
            "--coverage",
            str(workspace / "coverage.json"),
            "--purpose-reference",
            "owned-minimal-source-review",
            "--output",
            str(tmp_path / "remote-review.json"),
        )
        assert response.returncode == 0 and reviewed["status"] == "LOCAL_REVIEW_READY"
        response, finished = invoke(
            "finish",
            "--workspace",
            receipt["checked_handoff_workspace"],
            "--purpose-reference",
            "owned-minimal-completed-handoff",
            "--execute-local",
        )
        assert response.returncode == 0 and finished["status"] == "REVIEWED"
        for path in (root / ".cutback/agent").glob("*.json"):
            assert token not in path.read_text("utf-8")
        env["CUTBACK_CONTROL_TOKEN"] = tenant[2]["Authorization"][7:]
        response, denied = invoke(
            "proposal",
            "--purpose-reference",
            "owned-viewer-refusal",
            "--output",
            str(tmp_path / "viewer-output.json"),
        )
        assert response.returncode == 2
        assert not (tmp_path / "viewer-output.json").exists()
        assert receipt["native_sdk_calls"] == metadata["requests"] == observed["requests"]
