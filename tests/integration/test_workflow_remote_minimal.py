"""A separate HTTP-only agent process observes an actual approved collector run."""

import os
import subprocess
from uuid import UUID

import httpx
import pytest
from support_agent import native_response, prepare_fresh
from support_http import owned_collector
from support_remote_agent import remote_environment, remote_interpreter, remote_json

from cutback.agent import advance, load_session
from cutback.agent_control import Control
from cutback.core.workspace import write_private
from cutback.evals.executors import OpenAIExecutor
from cutback.server.db import ExperimentRunRecord
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_http_only_installed_agent_reconciles_same_run_without_runtime_dependencies(
    api,
    tenant,
    tmp_path,
    monkeypatch,
):
    configured = os.getenv("CUTBACK_TEST_REMOTE_PYTHON")
    if not configured:
        pytest.skip("set CUTBACK_TEST_REMOTE_PYTHON to a fresh cutback[prompts]-only installation")
    executable = remote_interpreter(configured)
    root, _, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch)
    env = remote_environment()
    probe = subprocess.run(
        [
            str(executable),
            "-I",
            "-c",
            (
                "import importlib.util,sys; from pathlib import Path; import cutback; "
                "assert Path(cutback.__file__).resolve().is_relative_to("
                "Path(sys.prefix).resolve()); "
                "assert all(importlib.util.find_spec(n) is None for n in "
                "('openai','anthropic','fastapi','uvicorn','sqlalchemy','alembic','psycopg',"
                "'cryptography','jsonschema','stripe','boto3','jwt','pytest'))"
            ),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert probe.returncode == 0, "REMOTE_AGENT_IS_NOT_A_MINIMAL_INDEPENDENT_INSTALLATION"
    with owned_collector(api[2]) as endpoint, httpx.Client(trust_env=False, timeout=30) as client:
        control = Control(endpoint, api[1]["Authorization"][7:], client=client)
        state = load_session(root)
        state["endpoint"] = endpoint
        write_private(root / ".cutback/agent/session.json", state)
        key = client.post(
            endpoint + f"/v1/projects/{tenant[0]}/keys",
            headers=api[1],
            json={"role": "project_admin", "environment": "staging"},
        )
        assert key.status_code == 201, "REMOTE_AGENT_SCOPED_KEY_CREATE_FAILED"
        scoped_token = key.json()["token"]
        env["CUTBACK_CONTROL_TOKEN"] = scoped_token
        write_private(
            root / ".cutback/agent/connection.json",
            {
                "endpoint": endpoint,
                "project": tenant[0],
            },
        )
        queued = advance(
            root, control, plan_hash=state["plan"]["plan_hash"], approval_ref="owned-minimal-client"
        )

        def invoke():
            response = subprocess.run(
                [
                    str(executable),
                    "-I",
                    "-m",
                    "cutback.cli",
                    "workflow",
                    "run",
                    "--target",
                    str(root),
                    "--max-polls",
                    "1",
                    "--max-wait-seconds",
                    "10",
                ],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            text = response.stdout + response.stderr
            if scoped_token in text or "Owned synthetic AA topic" in text:
                pytest.fail("REMOTE_AGENT_DISCLOSED_CREDENTIAL_OR_EXAMPLE", pytrace=False)
            return response, remote_json(response)

        response, waiting = invoke()
        assert response.returncode == 2, "REMOTE_AGENT_EXPECTED_ONE_POLL_WAIT_BOUND"
        assert waiting["operator"]["code"] == "LOCAL_WAIT_BOUND_REACHED"
        assert waiting["workflow"]["run_id"] == queued["run_id"]
        assert waiting["operator"]["local_worker_attempts"] == 0
        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-only")
        executor = OpenAIExecutor(
            http_client=httpx.Client(
                transport=httpx.MockTransport(native_response), trust_env=False
            )
        )
        try:
            result = run_one(
                api[2].state.sessions,
                api[2].state.artifact_store,
                executor=executor,
                execution_enabled=True,
                only_run_id=UUID(queued["run_id"]),
            )
        finally:
            executor.close()
        assert result["status"] == "COMPLETED", "OWNED_CLOSED_NATIVE_WORKER_DID_NOT_COMPLETE"
        response, completed = invoke()
        assert response.returncode == 0, "REMOTE_AGENT_DID_NOT_OBSERVE_COMPLETION"
        assert completed["operator"]["code"] == "STAGE_FINISHED"
        assert completed["workflow"]["run_id"] == queued["run_id"]
        assert completed["operator"]["local_worker_attempts"] == 0
        assert completed["operator"]["approval_performed"] is False
        assert completed["operator"]["queue_performed"] is False
        with api[2].state.sessions() as session:
            rows = session.query(ExperimentRunRecord).filter_by(project_id=UUID(tenant[0])).all()
            assert len(rows) == 1 and str(rows[0].id) == queued["run_id"]
            assert rows[0].requests_used == completed["workflow"]["report"]["requests"] > 0
        for path in (root / ".cutback/agent").glob("*.json"):
            assert scoped_token not in path.read_text("utf-8")
