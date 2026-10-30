"""CLI actions are explicit scoped control requests, never hidden provider calls."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_criteria import register_criteria


def test_cli_draft_reconcile_review_and_validation_have_distinct_actions(tmp_path):
    calls, outputs = [], []
    app = typer.Typer()

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "OWNED_CONTROL_RESPONSE"}

    def fail(*args):
        raise typer.Exit(2)

    register_criteria(app, lambda value, path=None: outputs.append(value), request, fail)
    project, criteria = str(uuid4()), str(uuid4())
    confirmation_request = str(uuid4())
    common = [
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "owned.json"),
    ]
    body = tmp_path / "request.json"
    body.write_text(json.dumps({"dataset_id": str(uuid4())}), encoding="utf-8")
    commands = [
        (["draft", str(body)], "POST", "/drafts"),
        (["show", criteria], "GET", "/" + criteria),
        (
            ["approve-generation", criteria, "a" * 64, "owned-approval"],
            "POST",
            "/" + criteria + "/approve-generation",
        ),
        (["queue", criteria, "a" * 64], "POST", "/" + criteria + "/runs"),
        (
            ["review", criteria, "b" * 64, "approve", "owned-review"],
            "POST",
            "/" + criteria + "/review",
        ),
        (["evaluate", criteria, str(body)], "POST", "/" + criteria + "/evaluation-draft"),
        (
            [
                "confirm",
                criteria,
                str(uuid4()),
                "candidate",
                "--max-usd",
                "1",
                "--max-requests",
                "100",
                "--request-id",
                confirmation_request,
            ],
            "POST",
            "/" + criteria + "/confirmation",
        ),
    ]
    for command, method, suffix in commands:
        result = CliRunner().invoke(app, ["criteria", *command, *common])
        assert result.exit_code == 0, result.output
        assert calls[-1][0][:3] == (
            method,
            "http://127.0.0.1:8080",
            f"/v1/projects/{project}/criteria" + suffix,
        )
        if command[0] == "confirm":
            assert calls[-1][1]["json"]["request_id"] == confirmation_request
    assert len(calls) == len(outputs) == len(commands)
    invalid = CliRunner().invoke(
        app, ["criteria", "review", criteria, "b" * 64, "auto-approve", "owned", *common]
    )
    assert invalid.exit_code == 2 and len(calls) == len(commands)
