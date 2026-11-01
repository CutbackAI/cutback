"""Public controls preserve approval boundaries and omit private validation details."""

from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_policy_program import register_policy_program


def command():
    app, result, errors = typer.Typer(), [], []

    def fail(code, action):
        errors.append((code, action))
        raise typer.Exit(2)

    register_policy_program(app, result.append, fail)
    return app, result, errors


def test_preparation_forwards_explicit_scope_and_budget_without_auto_approval(
    tmp_path, monkeypatch
):
    import cutback.agent_control as connection
    import cutback.agent_policy_program as program

    app, output, errors = command()
    control, project, calls = object(), str(uuid4()), []
    monkeypatch.setattr(connection, "connect", lambda *a, **kw: (control, project))

    def prepare(root, remote, owner, **kwargs):
        calls.append((root, remote, owner, kwargs))
        return {"status": "READY_FOR_APPROVAL"}

    monkeypatch.setattr(program, "prepare", prepare)
    monkeypatch.setattr(
        program, "advance", lambda *a, **kw: (_ for _ in ()).throw(AssertionError())
    )
    dataset, snapshot = str(uuid4()), str(uuid4())
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "prepare",
            "--target",
            str(tmp_path),
            "--dataset",
            dataset,
            "--baseline-snapshot",
            snapshot,
            "--producer",
            "app.py",
            "--purpose-reference",
            "owned-cli-fixture",
            "--max-usd",
            "0.012345678901",
            "--no-generate",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][1:3] == (control, project)
    assert calls[0][3]["max_usd"] == "0.012345678901"
    assert calls[0][3]["dataset_id"] == dataset
    assert calls[0][3]["baseline_snapshot_id"] == snapshot
    assert calls[0][3]["generate"] is False
    assert output == [{"status": "READY_FOR_APPROVAL"}] and not errors


def test_uncertain_approval_is_not_repeated_and_content_errors_are_redacted(tmp_path, monkeypatch):
    import cutback.agent_control as connection
    import cutback.agent_policy_program as program

    app, output, errors = command()
    calls = []
    monkeypatch.setattr(connection, "connect", lambda *a, **kw: (object(), str(uuid4())))

    def approve(*args, **kwargs):
        calls.append(kwargs)
        raise ValueError("PRIVATE_SYNTHETIC_INSTRUCTION: do not print this")

    monkeypatch.setattr(program, "advance", approve)
    result = CliRunner().invoke(
        app,
        [
            "source-policy",
            "continue",
            "--target",
            str(tmp_path),
            "--program-hash",
            "a" * 64,
            "--approval-reference",
            "owned-cli-approval",
        ],
    )
    assert result.exit_code == 2 and len(calls) == 1
    assert calls[0] == {"program_hash": "a" * 64, "approval_ref": "owned-cli-approval"}
    assert "PRIVATE_SYNTHETIC" not in result.output + str(errors)
    assert errors[0][0] == "POLICY_PROGRAM_ACTION_STOPPED" and not output
