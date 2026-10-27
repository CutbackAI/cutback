import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cutback.cli import app
from cutback.evals.importers import MAX_BYTES, prepare_import

ROOT = Path(__file__).resolve().parents[2]
PROFILE = "promptfoo-0.125.1-results.v3"


def producer():
    return json.loads(
        (ROOT / "tests/fixtures/imports/promptfoo-0.125.1-v3.json").read_text(encoding="utf-8")
    )


def test_real_producer_result_projection_does_not_promote_output_or_grade_to_independent_truth():
    raw = (ROOT / "tests/fixtures/imports/promptfoo-0.125.1-v3.json").read_bytes()
    prepared = prepare_import(raw, PROFILE)
    assert len(prepared.cases) == 2 and not prepared.rejected
    assert prepared.cases[0].input == "Synthetic urgent ticket"
    assert prepared.cases[0].gold == "urgent" and prepared.cases[0].reference == "urgent"
    assert all(a["evidence"] == "externally_asserted" for a in prepared.assertions)
    preview = prepared.preview()
    assert not preview["independent_labels"] and preview["provider_calls"] == 0
    assert "Synthetic urgent ticket" not in json.dumps(preview)
    assert "All assertions passed" not in json.dumps(preview)


def test_imported_scripts_urls_and_configs_are_projected_without_execution(tmp_path):
    data = producer()
    data["config"] = {
        "providers": ["file://never-import-this.py"],
        "env": {"secret": "SYNTHETIC_SECRET"},
    }
    row = data["results"]["results"][0]
    row["testCase"]["assert"] = [
        {
            "type": "javascript",
            "value": f"require('fs').writeFileSync({str(tmp_path / 'must-not-exist')!r}, 'bad')",
        }
    ]
    row["gradingResult"]["reason"] = "SYNTHETIC_SECRET"
    prepared = prepare_import(json.dumps(data).encode(), PROFILE)
    assert prepared.cases[0].gold is None
    assert prepared.projected_fields > 0 and not list(tmp_path.iterdir())
    assert "SYNTHETIC_SECRET" not in json.dumps(prepared.preview())


def test_provider_errors_are_not_application_quality_failures_and_scores_keep_decimal_precision():
    data = producer()
    one, two = data["results"]["results"]
    one.update(failureReason=2, success=False, score=None)
    one["response"] = {"error": "SYNTHETIC_SECRET"}
    raw = (
        json.dumps(data).replace('"score": 1', '"score": 0.12345678901234567890123456789').encode()
    )
    prepared = prepare_import(raw, PROFILE)
    assert prepared.assertions[0]["outcome"] == "error" and prepared.assertions[0]["score"] is None
    assert prepared.assertions[1]["score"] == "0.12345678901234567890123456789"
    assert "SYNTHETIC_SECRET" not in json.dumps(prepared.assertions)


def test_openinference_fixture_rejections_and_references_are_explicit():
    data = json.loads(
        (ROOT / "examples/workflow-tracing/openinference-v1.json").read_text(encoding="utf-8")
    )
    rows = data["resourceSpans"][0]["scopeSpans"][0]["spans"]
    rows[0]["attributes"].append(
        {"key": "output.value", "value": {"stringValue": "Historical synthetic output"}}
    )
    prepared = prepare_import(json.dumps(data).encode(), "openinference-otlp-json.v1")
    assert len(prepared.cases) == 3 and len(prepared.rejected) == 1
    assert prepared.cases[0].reference == "Historical synthetic output"
    assert all(c.gold is None for c in prepared.cases)
    assert prepared.preview()["historical_references"] == 1


def test_native_jsonl_rejects_unknown_fields_duplicates_and_lossy_structured_numbers():
    valid = {"id": "one", "input": "SYNTHETIC_CONTENT", "gold": {"amount": 1.25}}
    raw = "\n".join(
        json.dumps(r)
        for r in [valid, valid, {**valid, "id": "two", "unexpected": "SYNTHETIC_SECRET"}]
    ).encode()
    prepared = prepare_import(raw, "cutback-jsonl.v1")
    assert len(prepared.cases) == 1 and len(prepared.rejected) == 2
    assert prepared.cases[0].gold == {"amount": 1.25}
    assert "SYNTHETIC_CONTENT" not in json.dumps(prepared.preview())
    lossy = b'{"id":"one","input":"synthetic","gold":{"amount":0.12345678901234567890123456789}}'
    assert prepare_import(lossy, "cutback-jsonl.v1").rejected


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"x" * (MAX_BYTES + 1),
        b'{"id":"one","id":"two"}',
        b'{"value":NaN}',
        b"{" * 2000,
        b"[" * 25 + b"0" + b"]" * 25,
    ],
    ids=["empty", "oversized", "duplicate-key", "nonfinite", "malformed", "deep"],
)
def test_malformed_imports_never_echo_content(raw):
    with pytest.raises(ValueError) as error:
        prepare_import(raw, "cutback-jsonl.v1")
    assert len(str(error.value)) < 100


def test_cli_preview_is_offline_and_omits_content(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("preview must not contact any endpoint")

    monkeypatch.setattr("cutback.cli.request", no_network)
    path = tmp_path / "native.jsonl"
    path.write_text(
        '{"id":"one","input":"SYNTHETIC_CONTENT","reference":"unverified"}\n', encoding="utf-8"
    )
    result = CliRunner().invoke(
        app, ["datasets", "preview-import", str(path), "--profile", "cutback-jsonl.v1"]
    )
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output)
    assert preview["accepted"] == 1 and not preview["content_uploaded"]
    assert "SYNTHETIC_CONTENT" not in result.output
    missing = CliRunner().invoke(
        app, ["datasets", "preview-import", str(path), "--profile", "unknown"]
    )
    assert missing.exit_code != 0 and "IMPORT_PREVIEW_REJECTED" in missing.output
