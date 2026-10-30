"""Preparing a tool plan never queues execution or approves a paid budget."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_tools import register_tools


def test_scoped_tool_contract_is_required_before_preparation_and_metadata_is_default(tmp_path):
    project, principal, broker, run, session = [str(uuid4()) for _ in range(5)]
    app, calls, outputs = typer.Typer(), [], []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"prepared": True}

    def fail(code, message):
        outputs.append(code)
        raise typer.Exit(2)

    register_tools(app, lambda value, path: outputs.append(value), request, fail)
    contract = tmp_path / "contract.json"
    raw = {
        "project_id": project,
        "principal_id": principal,
        "broker_id": broker,
        "environment": "staging",
        "image_id": "sha256:" + "a" * 64,
        "content_use_approved": True,
    }
    contract.write_text(json.dumps(raw), encoding="utf-8")
    common = [
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "receipt.json"),
    ]
    runner = CliRunner()
    result = runner.invoke(app, ["tools", "prepare", str(uuid4()), str(contract), *common])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][0][2].endswith("/experiments/draft")
    body = calls[0][1]["json"]
    assert body["max_usd"] == "0" and body["isolated_tools"]["content_use_approved"] is True
    contract.write_text(json.dumps({**raw, "project_id": str(uuid4())}), encoding="utf-8")
    assert (
        runner.invoke(app, ["tools", "prepare", str(uuid4()), str(contract), *common]).exit_code
        == 2
    )
    assert len(calls) == 1
    assert runner.invoke(app, ["tools", "sessions", run, *common]).exit_code == 0
    assert calls[-1][0][0] == "GET" and calls[-1][1].get("json") is None
    assert (
        runner.invoke(
            app, ["tools", "contain", run, session, *common, "--approval-hash", "wrong"]
        ).exit_code
        == 2
    )
    assert len(calls) == 2
    assert (
        runner.invoke(
            app, ["tools", "contain", run, session, *common, "--approval-hash", "a" * 64]
        ).exit_code
        == 0
    )
    assert calls[-1][0][2].endswith("/containment")
    assert calls[-1][1]["json"] == {"approval_hash": "a" * 64}
