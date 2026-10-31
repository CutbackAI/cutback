import json
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


def test_agent_change_materialization_rechecks_exact_artifact_and_source(tmp_path):
    from support_retrieval_application import application

    from cutback.core.retrieval import RetrievalSettings
    from cutback.retrieval_changes import RetrievalProposal, inspect_configuration, prepare_patch

    root = application(tmp_path)
    baseline, binding = inspect_configuration(root, "retrieval.json")
    artifact = prepare_patch(
        root,
        RetrievalProposal(
            run_id=uuid4(),
            plan_hash="a" * 64,
            dataset_hash="b" * 64,
            binding=binding,
            baseline=baseline,
            proposed=RetrievalSettings(chunk_chars=256, overlap_chars=0, top_k=1),
            decision="synthetic_local_review_only",
        ),
    )
    original = (root / "retrieval.json").read_bytes()
    file = tmp_path / "agent-change.json"
    file.write_text(json.dumps(artifact), encoding="utf-8")
    target = tmp_path / "materialized.json"
    app = typer.Typer()
    results = []

    def fail(*args):
        raise typer.Exit(2)

    register_retrieval(app, lambda value, path=None: results.append(value), None, fail)
    command = ["retrieval", "patch", str(root), str(file), "--output", str(target)]
    assert CliRunner().invoke(app, command).exit_code == 0
    assert target.with_suffix(".patch").read_bytes() == artifact["diff"].encode()
    assert (root / "retrieval.json").read_bytes() == original
    artifact["diff"] += "unauthorized extra patch\n"
    file.write_text(json.dumps(artifact), encoding="utf-8")
    assert CliRunner().invoke(app, command).exit_code == 2
    assert len(results) == 1
