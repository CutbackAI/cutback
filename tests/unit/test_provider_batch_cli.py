"""Core CLI forwards explicit recovery metadata without SDK calls or key discovery."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_batch import register_batch


def test_batch_recovery_hints_are_bounded_and_forwarded_only_after_valid_json(tmp_path):
    app, calls, errors = typer.Typer(), [], []

    def fail(code, action):
        errors.append(code)
        raise typer.Exit(2)

    register_batch(
        app, lambda value, path: None, lambda *args, **kwargs: calls.append(kwargs), fail
    )
    hint_file = tmp_path / "recovery.json"
    args = [
        "batch",
        "reconcile",
        str(uuid4()),
        "--project",
        str(uuid4()),
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "result.json"),
        "--hints",
        str(hint_file),
    ]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 2 and not calls
    hint_file.write_bytes(b"x" * 8193)
    assert runner.invoke(app, args).exit_code == 2 and not calls
    hint_file.write_text("[]", encoding="utf-8")
    assert runner.invoke(app, args).exit_code == 2 and not calls
    hints = {"hints": [{"model_key": "baseline", "file_id": "file-original"}]}
    hint_file.write_text(json.dumps(hints), encoding="utf-8")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert calls == [{"json": hints}]
    assert errors == ["BATCH_RECOVERY_HINT_FILE_INVALID"] * 3
