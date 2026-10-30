"""Explicit core-only rendering and prepared dataset requests never infer authority."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_compression import register_compression


def test_offline_render_reads_only_selected_context_and_keeps_unknown_quality(tmp_path):
    calls, results = [], []
    app = typer.Typer()

    def forbidden(*args, **kwargs):
        calls.append(1)
        raise AssertionError("OFFLINE_RENDER_CANNOT_CONTACT_COLLECTOR")

    def fail(*args):
        raise typer.Exit(2)

    register_compression(app, lambda value, path=None: results.append(value), forbidden, fail)
    source = tmp_path / "context.json"
    source.write_text(
        json.dumps({"query": "deadline", "context": "deadline 14\ndeadline 14\n"}), encoding="utf-8"
    )
    settings = tmp_path / "compression.json"
    settings.write_text('{"mode":"deduplicate_lines","max_context_bytes":1024}', encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "compression",
            "render",
            str(source),
            str(settings),
            "--output",
            str(tmp_path / "rendered.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        not calls
        and results[0]["quality_preserved"] is None
        and results[0]["token_reduction"] is None
    )
    assert results[0]["output_context_bytes"] < results[0]["input_context_bytes"]


def test_draft_uses_explicit_existing_dataset_destination_without_approval_or_inference(tmp_path):
    calls, results = [], []
    app = typer.Typer()

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "READY_FOR_APPROVAL"}

    def fail(*args):
        raise typer.Exit(2)

    register_compression(app, lambda value, path=None: results.append(value), request, fail)
    source = tmp_path / "draft.json"
    source.write_text(json.dumps({"dataset_id": str(uuid4())}), encoding="utf-8")
    project = str(uuid4())
    result = CliRunner().invoke(
        app,
        [
            "compression",
            "draft",
            str(source),
            "--project",
            project,
            "--endpoint",
            "http://127.0.0.1:8080",
            "--output",
            str(tmp_path / "plan.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][0][2] == f"/v1/projects/{project}/compression/draft"
    assert results == [{"status": "READY_FOR_APPROVAL"}]
