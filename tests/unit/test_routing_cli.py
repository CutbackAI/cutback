"""Routing consent boundaries remain separate and private reads need an explicit file."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_routing import register_routing


def commands():
    app = typer.Typer()
    calls, outputs = [], []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"id": "owned-metadata", "content": {"private": "owned-private-fit"}}

    def fail(*args):
        raise typer.Exit(2)

    register_routing(app, lambda value, destination=None: outputs.append(value), request, fail)
    return app, calls, outputs


def test_routing_cli_preserves_exact_request_id_and_separate_approvals(tmp_path):
    app, calls, outputs = commands()
    project, fit, plan = [str(uuid4()) for _ in range(3)]
    request_file = tmp_path / "request.json"
    body = {"request_id": str(uuid4()), "purpose": "owned-routing"}
    request_file.write_text(json.dumps(body), encoding="utf-8")
    common = [
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "metadata.json"),
    ]
    for command, suffix in [
        (["training-draft", str(request_file)], "/routing/training-drafts"),
        (["fit", str(request_file)], "/routing/fits"),
        (["validation-draft", fit, str(request_file)], f"/routing/fits/{fit}/validation-drafts"),
        (
            ["confirmation-draft", fit, str(request_file)],
            f"/routing/fits/{fit}/confirmation-drafts",
        ),
    ]:
        for _ in range(2):
            result = CliRunner().invoke(app, ["routing", *command, *common])
            assert result.exit_code == 0, result.output
            assert calls[-1] == (
                ("POST", "http://127.0.0.1:8080", f"/v1/projects/{project}" + suffix),
                {"json": body},
            )
    result = CliRunner().invoke(
        app, ["routing", "approve", plan, "a" * 64, "owned-approval", *common]
    )
    assert result.exit_code == 0, result.output
    assert calls[-1][1]["json"] == {"plan_hash": "a" * 64, "approval_ref": "owned-approval"}
    result = CliRunner().invoke(app, ["routing", "queue", plan, *common])
    assert result.exit_code == 0, result.output
    assert calls[-1][1]["json"] == {"plan_id": plan}


def test_private_fit_read_requires_output_before_connecting(tmp_path):
    app, calls, outputs = commands()
    result = CliRunner().invoke(
        app,
        [
            "routing",
            "content",
            str(uuid4()),
            "--project",
            str(uuid4()),
            "--endpoint",
            "http://127.0.0.1:8080",
            "--purpose-reference",
            "owned-review",
        ],
    )
    assert result.exit_code == 2 and not calls and not outputs
    destination = tmp_path / "private.json"
    result = CliRunner().invoke(
        app,
        [
            "routing",
            "content",
            str(uuid4()),
            "--project",
            str(uuid4()),
            "--endpoint",
            "http://127.0.0.1:8080",
            "--purpose-reference",
            "owned-review",
            "--output",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.output
    assert not outputs and "owned-private-fit" not in result.output
    assert (
        json.loads(destination.read_text(encoding="utf-8"))["content"]["private"]
        == "owned-private-fit"
    )


def test_invalid_reconciliation_id_and_plan_hash_never_connect(tmp_path):
    app, calls, _ = commands()
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"request_id": "not-a-uuid"}), encoding="utf-8")
    common = [
        "--project",
        str(uuid4()),
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "metadata.json"),
    ]
    assert CliRunner().invoke(app, ["routing", "fit", str(path), *common]).exit_code == 2
    assert (
        CliRunner()
        .invoke(app, ["routing", "approve", str(uuid4()), "invalid", "owned", *common])
        .exit_code
        == 2
    )
    assert not calls
