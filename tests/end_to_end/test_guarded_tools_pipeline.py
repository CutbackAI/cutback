"""Actual checker/broker processes, Docker SQL, PostgreSQL and authenticated browser."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from end_to_end.test_pipeline import server
from support_browser import browser_command

from cutback.demo_isolated_tools import seed_isolated_tools
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("CUTBACK_TEST_TOOL_ISOLATION") != "true",
        reason="explicit owned Docker guarded-tool acceptance",
    ),
]


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


@pytest.mark.parametrize("deny_output", [False, True])
def test_separate_checker_and_broker_preserve_known_sql_writes_and_withheld_replies(
    api, tenant, tmp_path, monkeypatch, deny_output
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    receipt = seed_isolated_tools(
        app,
        api[0],
        tenant[0],
        os.environ["CUTBACK_TEST_TOOL_IMAGE_ID"],
        headers=api[1],
        guarded=True,
        deny_tool_output=deny_output,
    )
    assert receipt["sessions"] == receipt["state_changes"] == 4
    assert receipt["operations"] == 8 and receipt["cleanup_confirmed"]
    assert receipt["paid_provider_calls"] == 0 and receipt["complete_task_cost_usd"] is None
    guard = receipt["runtime_guardrails"]
    assert guard["checks"] == 16 and guard["blocked"] == (4 if deny_output else 0)
    assert guard["withheld_operations"] == (4 if deny_output else 0)
    assert guard["authorized_operations"] == (4 if deny_output else 8)
    state = api[0].get(
        f"/v1/projects/{tenant[0]}/runtime-guardrails/{guard['policy_id']}", headers=tenant[2]
    )
    assert state.status_code == 200 and state.json()["state"] == "DISABLED"
    assert state.json()["execution_authority"] == "inactive"
    if not deny_output or os.getenv("CUTBACK_TEST_BROWSER") != "true":
        return
    # A fresh approval is required for another plan. Never mutate the sample's
    # retired policy or claim that planning dispatches the checker/model.
    base = f"/v1/projects/{tenant[0]}"
    definition = api[0].post(
        base + f"/runtime-guardrails/{guard['policy_id']}/content",
        headers=api[1],
        json={"purpose": "owned-browser-plan-review"},
    )
    assert definition.status_code == 200
    raw = definition.json()
    raw.update(policy_id=str(uuid4()), content_nonce=str(uuid4()))
    registered = api[0].post(
        base + "/runtime-guardrails",
        headers=api[1],
        json={
            "service_id": str(uuid4()),
            "capture_policy_id": state.json()["capture_policy_id"],
            "policy": raw,
            "max_checks": 64,
        },
    )
    assert registered.status_code == 201, registered.text
    approved = api[0].post(
        base + f"/runtime-guardrails/{registered.json()['id']}/approve",
        headers=api[1],
        json={
            "approval_hash": registered.json()["approval_hash"],
            "expected_state_version": registered.json()["state_version"],
            "approval_ref": str(uuid4()),
        },
    )
    assert approved.status_code == 200
    with server(app) as endpoint:
        completed = subprocess.run(
            browser_command(Path(__file__).resolve().parents[2] / "web", "guarded-tools.spec.ts"),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_UI_KEY": api[1]["Authorization"][7:],
                "CUTBACK_UI_VIEWER": tenant[2]["Authorization"][7:],
                "CUTBACK_GUARDED_TOOL_RUN": receipt["run_id"],
                "CUTBACK_TOOL_GUARD_POLICY": approved.json()["id"],
                "CUTBACK_TOOL_DATASET": receipt["dataset_id"],
                "CUTBACK_TOOL_PRINCIPAL": receipt["principal_id"],
                "CUTBACK_TOOL_BROKER": receipt["broker_id"],
                "CUTBACK_UI_OUTPUT_DIR": str(tmp_path / "browser"),
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    output = completed.stdout + completed.stderr
    for credential in (api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]):
        output = output.replace(credential, "<credential-omitted>")
    assert completed.returncode == 0, output
