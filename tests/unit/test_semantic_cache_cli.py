"""No SDK import, silent content upload, blind append retry or mismatched resumption."""

import json
from uuid import uuid4

import typer
from typer.testing import CliRunner

from cutback.cli_semantic_cache import register_semantic_cache
from cutback.evals.datasets import Case, digest


def test_upload_consent_chunking_persisted_cursor_and_exact_resume(tmp_path):
    app, calls, saved = typer.Typer(), [], []
    project, namespace, upload = (str(uuid4()) for _ in range(3))
    cases = [
        Case(
            id="case-" + str(index), input="Owned control " + chr(0x400 + index), gold="billing"
        ).model_dump(mode="json")
        for index in range(17)
    ]
    body = {
        "workflow": "classify",
        "environment": "staging",
        "policy_id": str(uuid4()),
        "source_type": "synthetic_fixture",
        "provenance_ref": "owned-fixture",
        "cases": cases,
    }
    source = tmp_path / "input.json"
    source.write_text(json.dumps(body), encoding="utf-8")
    receipt = {
        "id": upload,
        "namespace_id": namespace,
        "state": "STAGING",
        "received_cases": 0,
        "approval_hash": "b" * 64,
        "specification": {
            "upload": {"expected_cases_sha256": digest(cases), "expected_cases": len(cases)}
        },
    }

    def request(method, endpoint, path, **kwargs):
        calls.append((method, path, kwargs))
        if path.endswith("/append"):
            assert saved  # UUID receipt was persisted before any sensitive content.
            assert kwargs["json"]["offset"] == receipt["received_cases"]
            receipt["received_cases"] += len(kwargs["json"]["cases"])
        if path.endswith("/finalize"):
            receipt["state"] = "FINALIZING"
        return dict(receipt)

    def fail(code, action):
        raise typer.Exit(2)

    register_semantic_cache(app, lambda value, path: saved.append(dict(value)), request, fail)
    args = [
        "semantic-cache",
        "upload-dataset",
        str(source),
        namespace,
        "--project",
        project,
        "--endpoint",
        "http://127.0.0.1:8080",
        "--output",
        str(tmp_path / "receipt.json"),
    ]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 2 and not calls
    result = runner.invoke(app, [*args, "--authorize-content-upload"])
    assert result.exit_code == 0, result.output
    chunks = [call[2]["json"]["cases"] for call in calls if call[1].endswith("/append")]
    assert [len(chunk) for chunk in chunks] == [8, 8, 1]
    before = len(calls)
    assert (
        runner.invoke(
            app, [*args, "--authorize-content-upload", "--resume-upload", upload]
        ).exit_code
        == 0
    )
    assert len(calls) == before + 1 and calls[-1][0] == "GET"
    receipt["specification"]["upload"]["expected_cases_sha256"] = "c" * 64
    assert (
        runner.invoke(
            app, [*args, "--authorize-content-upload", "--resume-upload", upload]
        ).exit_code
        == 2
    )
