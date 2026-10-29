from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_retrieval import register_retrieval


def test_required_named_options_and_explicit_corpus_upload_authority(tmp_path):
    calls, results = [], []
    app = typer.Typer()

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "READY_FOR_APPROVAL"}

    def fail(*args):
        raise typer.Exit(2)

    register_retrieval(app, lambda value, path=None: results.append(value), request, fail)
    file = tmp_path / "authorized-draft.json"
    file.write_text("{}", encoding="utf-8")
    project = str(uuid4())
    args = [
        "retrieval",
        "draft",
        str(file),
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "plan.json"),
    ]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 2 and not calls
    result = runner.invoke(app, [*args, "--authorize-corpus-upload"])
    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and calls[0][0][2] == f"/v1/projects/{project}/retrieval/draft"
    assert results == [{"status": "READY_FOR_APPROVAL"}]
