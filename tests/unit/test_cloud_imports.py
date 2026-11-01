"""Cloud exports preserve their asserted evidence without executing or normalizing it."""

import builtins
import json
from pathlib import Path

import pytest

from cutback.evals.importers import (
    SourceImportError,
    assertion_metadata,
    assertion_provenance,
    prepare_import,
)

ROOT = Path(__file__).resolve().parents[2] / "tests/fixtures/imports"
PROFILES = (
    ("bedrock-automated-model-jsonl.v1", "bedrock-automated-model.jsonl", 1, 1),
    ("azure-ai-evaluation-1.18.10-results.v1", "azure-ai-evaluation-1.18.10-results.json", 2, 4),
    ("vertexai-1.153.1-metrics-jsonl.v1", "vertexai-1.153.1-metrics.jsonl", 2, 2),
)


@pytest.mark.parametrize("profile,filename,cases,metrics", PROFILES)
def test_qualified_owned_exports_remain_unreviewed_and_do_not_normalize_scores(
    profile, filename, cases, metrics, monkeypatch
):
    original = builtins.__import__

    def offline(name, *args, **kwargs):
        if name.startswith(("azure", "vertexai", "google", "boto3", "botocore")):
            raise AssertionError("IMPORTER_TRIED_TO_LOAD_VENDOR_SDK")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", offline)
    result = prepare_import((ROOT / filename).read_bytes(), profile)
    assert len(result.cases) == cases and len(result.assertions) == metrics and not result.rejected
    assert all(
        row["evidence"] == "externally_asserted" and row["scale"] is None
        for row in result.assertions
    )
    assert all(
        row["outcome"] == "reported" and row["normalization"] == "none" for row in result.assertions
    )
    preview = result.preview()
    assert preview["independent_labels"] is preview["content_uploaded"] is False
    assert preview["provider_calls"] == 0
    assert "Synthetic ticket" not in json.dumps(preview)
    if profile.startswith("bedrock"):
        assert result.assertions[0]["source_model_identifier"] == "synthetic-model-identifier"
        assert "synthetic-model-identifier" not in json.dumps(assertion_metadata(result.assertions))


def test_missing_scores_and_booleans_cannot_become_numeric_quality_or_business_success():
    unknown = prepare_import((ROOT / "vertexai-1.153.1-missing.jsonl").read_bytes(), PROFILES[2][0])
    value = unknown.assertions[0]
    assert (
        value["outcome"] == "missing" and value["score"] is None and value["boolean_value"] is None
    )
    assert value["value_kind"] == "missing" and value["error_code"] == "SOURCE_METRIC_MISSING"
    azure = prepare_import((ROOT / PROFILES[1][1]).read_bytes(), PROFILES[1][0])
    boolean = [r for r in azure.assertions if r["value_kind"] == "boolean"]
    assert [row["boolean_value"] for row in boolean] == [True, False]
    assert all(row["score"] is None and row["outcome"] == "reported" for row in boolean)


def test_score_precision_stays_as_serialized_in_the_source():
    raw = (ROOT / PROFILES[0][1]).read_text().replace("0.75", "0.12345678901234567890123456789")
    value = prepare_import(raw.encode(), PROFILES[0][0]).assertions[0]
    assert value["score"] == "0.12345678901234567890123456789"


@pytest.mark.parametrize("status,expected", [("error", "error"), ("skipped", "skipped")])
def test_explicit_evaluator_state_overrides_a_stale_success_score_without_copying_error_text(
    status, expected
):
    row = {
        "prompt": "Owned example",
        "response": "Historical",
        "accuracy/score": 1,
        "accuracy/status": status,
        "accuracy/error": "SYNTHETIC_ERROR_BODY" if status == "error" else None,
    }
    result = prepare_import(json.dumps(row).encode(), PROFILES[2][0])
    value = result.assertions[0]
    assert value["outcome"] == expected and value["score"] is None
    assert value["discarded_reported_score"] == "1"
    assert "SYNTHETIC_ERROR_BODY" not in json.dumps(result.assertions)
    assert "discarded_reported_score" not in assertion_metadata(result.assertions)[0]


def test_metric_names_are_permitted_content_not_dataset_metadata_or_preview_copy():
    name = "SYNTHETIC_METRIC_CONTENT_MUST_STAY_ENCRYPTED"
    row = {"prompt": "Owned example", "response": "Historical", name + "/score": 5}
    result = prepare_import(json.dumps(row).encode(), PROFILES[2][0])
    assert result.assertions[0]["metric_name"] == name
    assert name not in json.dumps(result.preview()) + json.dumps(
        assertion_metadata(result.assertions)
    )
    assert assertion_metadata(result.assertions)[0]["score"] == "5"
    assert assertion_metadata(result.assertions)[0]["metric_id"].startswith("metric:")


def test_source_context_is_kept_in_explicit_input_data_and_aggregate_only_files_are_refused():
    body = json.loads((ROOT / PROFILES[1][1]).read_text())
    body["rows"][0]["inputs.context"] = "Owned exact supporting context"
    result = prepare_import(json.dumps(body).encode(), PROFILES[1][0])
    assert json.loads(result.cases[0].input) == {
        "query": body["rows"][0]["inputs.query"],
        "context": "Owned exact supporting context",
    }
    with pytest.raises(SourceImportError):
        prepare_import(b'{"metrics":{"accuracy":1}}', PROFILES[1][0])
    body["oai_eval_run_ids"] = []
    with pytest.raises(SourceImportError, match="NATIVE_GRADER_RESULTS_REQUIRE_SEPARATE_PROFILE"):
        prepare_import(json.dumps(body).encode(), PROFILES[1][0])


def test_repeated_input_rows_retain_distinct_cases_in_one_family():
    row = {"prompt": "Owned repeated input", "response": "Historical", "accuracy/score": 1}
    raw = "\n".join(json.dumps(row) for _ in range(2)).encode()
    result = prepare_import(raw, PROFILES[2][0])
    assert len(result.cases) == 2 and result.cases[0].id != result.cases[1].id
    assert result.cases[0].group == result.cases[1].group


def test_legacy_promptfoo_assertion_metadata_is_unchanged():
    result = prepare_import(
        (ROOT / "promptfoo-0.125.1-v3.json").read_bytes(), "promptfoo-0.125.1-results.v3"
    )
    assert assertion_metadata(result.assertions) == result.assertions


@pytest.mark.parametrize("value", ["1", [], {}, "SYNTHETIC_PRIVATE_SCORE"])
def test_unsupported_score_shapes_have_fixed_diagnostics(value):
    row = {"prompt": "Owned input", "response": "Historical", "accuracy/score": value}
    result = prepare_import(json.dumps(row).encode(), PROFILES[2][0])
    assert not result.cases and result.rejected == [
        {"row": 0, "code": "CLOUD_FINITE_SCORE_BOOLEAN_OR_NULL_REQUIRED"}
    ]
    assert "SYNTHETIC_PRIVATE_SCORE" not in json.dumps(result.preview())


def test_imports_cannot_expand_small_source_rows_into_unbounded_metric_records():
    row = {
        "prompt": "Owned repeated input",
        "response": "Historical",
        **{f"m{i}/score": 1 for i in range(64)},
    }
    result = prepare_import("\n".join(json.dumps(row) for _ in range(160)).encode(), PROFILES[2][0])
    assert len(result.assertions) <= 10000 and result.rejected
    assert {r["code"] for r in result.rejected} == {"IMPORT_METRIC_TOTAL_BOUND_EXCEEDED"}
    metadata = assertion_provenance(result.assertions)
    assert len(metadata["source_asserted_checks"]) == 25
    assert metadata["source_asserted_checks_total"] == len(result.assertions)
    assert metadata["source_asserted_checks_truncated"] is True
    assert "not_task_or_population_coverage" in metadata["source_asserted_checks_scope"]


@pytest.mark.parametrize("number", ["1e1000000", "-1e1000000", "1.000000000000000000001e20"])
def test_score_bounds_do_not_overflow_or_round_under_the_callers_decimal_context(number):
    from decimal import localcontext

    raw = ('{"prompt":"Owned","response":"x","metric/score":' + number + "}").encode()
    with localcontext() as context:
        context.prec = 1
        result = prepare_import(raw, PROFILES[2][0])
    assert not result.cases and result.rejected == [
        {"row": 0, "code": "CLOUD_FINITE_SCORE_BOOLEAN_OR_NULL_REQUIRED"}
    ]
    assert number not in json.dumps(result.preview())


def test_unrepresentable_json_exponent_has_a_fixed_content_free_file_diagnostic():
    raw = b'{"prompt":"Owned","response":"x","metric/score":1e999999999999999999999999}'
    with pytest.raises(SourceImportError, match="^MALFORMED_OR_DUPLICATE_IMPORT_JSON$"):
        prepare_import(raw, PROFILES[2][0])
