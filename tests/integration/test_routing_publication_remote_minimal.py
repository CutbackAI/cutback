"""Real routing authority refuses synthetic/revoked publication before external I/O."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.agent import load_session
from cutback.core.workspace import write_private
from cutback.demo_routing_program import run_sample
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import CapturePolicyRecord

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("simple_controls", [False, True])
def test_http_only_routing_publisher_rechecks_real_scope_without_spend_or_external_io(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    simple_controls,
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("matching core-plus-HTTPX wheel required for remote routing publication")
    executable, env = remote_interpreter(configured), remote_environment()
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            "import importlib.util,sys; from pathlib import Path; import cutback; "
            "import cutback.agent_routing_publication,cutback.routing_publication; "
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
    assert probe.returncode == 0, "MATCHING_MINIMAL_ROUTING_PUBLICATION_INSTALL_REQUIRED"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=30,
        ) as client,
    ):
        result = run_sample(
            api[2],
            client,
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
            simple_controls_pass=simple_controls,
        )
        # The checked handoff copy deliberately excludes ignored agent state.
        # Resume the original application's saved program, as an installed
        # remote agent would, and configure its explicit collector destination.
        root = Path(result["workspace"]) / "application"
        key = client.post(
            endpoint + f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin", "environment": "staging"},
        )
        assert key.status_code == 201
        token = key.json()["token"]
        write_private(
            root / ".cutback/agent/connection.json", {"endpoint": endpoint, "project": tenant[0]}
        )
        env["CUTBACK_CONTROL_TOKEN"] = token
        env["CUTBACK_GITHUB_TOKEN"] = "owned-synthetic-no-network-token"
        launch = (
            "import httpx,runpy,sys\n"
            "original=httpx.Client.send\n"
            "def owned(self,request,*args,**kwargs):\n"
            " assert request.url.host=='127.0.0.1' and request.url.port=="
            + str(urlsplit(endpoint).port)
            + ", 'EXTERNAL_NETWORK_FORBIDDEN_IN_OWNED_TEST'\n"
            " return original(self,request,*args,**kwargs)\n"
            "httpx.Client.send=owned\n"
            "sys.argv=['cutback','routing','program','publish',*sys.argv[1:]]\n"
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
                    "example-owner/routing-app",
                    "--head-branch",
                    "cutback/checked-routing",
                    "--purpose-reference",
                    "owned-minimal-routing-publication",
                    "--publish",
                ],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if any(
                value in response.stdout + response.stderr
                for value in (
                    token,
                    env["CUTBACK_GITHUB_TOKEN"],
                    env["CUTBACK_CONTROL_TOKEN"],
                )
            ):
                pytest.fail("ROUTING_CLIENT_CREDENTIAL_ESCAPED", pytrace=False)
            assert "EXTERNAL_NETWORK_FORBIDDEN_IN_OWNED_TEST" not in response.stderr
            assert response.returncode != 0
            assert not (root / ".cutback/agent/routing-program-publication.json").exists()
            return remote_json(response)

        assert invoke()["code"] == "REVIEW_PUBLICATION_ROUTING_HELDOUT_REVIEW_REQUIRED"
        env["CUTBACK_CONTROL_TOKEN"] = tenant[2]["Authorization"][7:]
        invoke()
        env["CUTBACK_CONTROL_TOKEN"] = token
        state = load_session(root)
        with api[2].state.sessions.begin() as session:
            session.get(CapturePolicyRecord, UUID(state["policy_id"])).enabled = False
        assert invoke()["code"] != "REVIEW_PUBLICATION_ROUTING_HELDOUT_REVIEW_REQUIRED"
        final = client.get(
            endpoint + f"/v1/projects/{tenant[0]}/routing/programs/{result['program_id']}",
            headers=api[1],
        )
        assert final.status_code == 200 and final.json()["requests"] == result["native_sdk_calls"]
        for path in (root / ".cutback/agent").glob("*.json"):
            if token in path.read_text("utf-8") or env["CUTBACK_GITHUB_TOKEN"] in path.read_text(
                "utf-8"
            ):
                pytest.fail("ROUTING_CLIENT_CREDENTIAL_PERSISTED", pytrace=False)
