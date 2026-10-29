"""Ordinary source preparation and actual CLI over a scoped owned test database."""

import base64
import json

import pytest
from support_agent import prepare_fresh
from test_agent_campaign import baseline
from test_campaign_workflow import native
from typer.testing import CliRunner

from cutback.agent_campaign import advance, prepare, status
from cutback.cli import app

pytestmark = pytest.mark.postgres


def setup_local(api, tenant, tmp_path, monkeypatch, postgres_url):
    from cutback.evals import artifacts

    captured = {}
    store_class = artifacts.EncryptedFileStore

    def capture_store(path, key, **kwargs):
        captured.update(root=path, key=key)
        return store_class(path, key, **kwargs)

    monkeypatch.setattr(artifacts, "EncryptedFileStore", capture_store)
    root, control, state, _ = prepare_fresh(api, tenant, tmp_path, monkeypatch, for_campaign=True)
    monkeypatch.setattr(artifacts, "EncryptedFileStore", store_class)
    row = prepare(
        root,
        control,
        baseline_snapshot_id=baseline(api, tenant, state["selected_workflow"]),
        max_usd="10",
        max_requests=1000,
        savings_per_success="0.01",
        forecast_successful_tasks=10000,
    )
    advance(root, control, campaign_hash=row["campaign_hash"], approval_ref="owned-cli-test")
    configuration = {
        "endpoint": control.endpoint,
        "allow_payloads": True,
        "payload_key": base64.b64encode(captured["key"]).decode(),
    }
    monkeypatch.setattr("cutback.agent_control.connect", lambda target: (control, {}))
    monkeypatch.setattr("cutback.local_stack.configured", lambda target: (tmp_path, configuration))
    monkeypatch.setattr("cutback.local_stack.database_url", lambda location, config: postgres_url)
    calls = []

    def configured_executor():
        executor, requests = native(monkeypatch)
        calls.append(requests)
        return executor

    monkeypatch.setattr("cutback.evals.executors.ConfiguredExecutor", configured_executor)
    return root, control, row, configuration, calls


def test_actual_cli_drives_ordinary_preparation_and_keeps_credentials_in_memory(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, control, row, _, calls = setup_local(api, tenant, tmp_path, monkeypatch, postgres_url)
    original = (root / "app.py").read_bytes()
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    result = CliRunner().invoke(app, ["campaign", "run", "--target", str(root)])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.stdout)
    assert receipt["event"] == "campaign_result" and receipt["status"] == "COMPLETED"
    assert result.stderr.strip()
    assert all(
        json.loads(line)["event"] == "campaign_progress" for line in result.stderr.splitlines()
    )
    assert '"event": "campaign_result"' in result.output
    assert '"status": "COMPLETED"' in result.output
    assert "synthetic_confirmation_passed_real_data_required" in result.output
    assert control.token not in result.output
    assert "Owned synthetic classification task" not in result.output
    assert "synthetic-unused-key" not in result.output
    import os

    assert os.environ["CUTBACK_ALLOW_PAYLOADS"] == "false"
    current = status(root, control)
    assert current["id"] == row["id"] and current["campaign_hash"] == row["campaign_hash"]
    assert current["billing_complete"]
    assert current["requests"] == sum(len(requests) for requests in calls)
    assert len(calls) == 2 and not any("text" in request for group in calls for request in group)
    assert (root / "app.py").read_bytes() == original
    assert control.token not in json.dumps(current)


@pytest.mark.parametrize("command", ["work", "run"])
def test_actual_local_commands_reject_changed_source_before_executor_construction(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
    command,
):
    root, control, row, _, calls = setup_local(api, tenant, tmp_path, monkeypatch, postgres_url)
    with (root / "app.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# source changed after approval\n")
    result = CliRunner().invoke(app, ["campaign", command, "--target", str(root)])
    assert result.exit_code != 0 and "SOURCE_CHANGED" in result.output
    assert not calls
    current = status(root, control)
    assert current["id"] == row["id"] and current["status"] == "QUEUED"
    assert current["requests"] == 0 and not current["runs"]


def test_local_operator_rejects_a_different_configured_destination(
    api,
    tenant,
    tmp_path,
    monkeypatch,
    postgres_url,
):
    root, _, _, config, calls = setup_local(api, tenant, tmp_path, monkeypatch, postgres_url)
    config["endpoint"] = "http://127.0.0.1:54321"
    result = CliRunner().invoke(app, ["campaign", "run", "--target", str(root)])
    assert result.exit_code != 0 and "LOCAL_OPERATOR_SCOPE_MISMATCH" in result.output
    assert not calls
