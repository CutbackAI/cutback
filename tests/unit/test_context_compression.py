"""Compression changes actual inputs; quality and complete economics gate selection."""

import json

import pytest
from pydantic import ValidationError
from support_compression import NativeBoundary, fixture

from cutback.core.compression import CompressionSettings, ContextInput, compress_context
from cutback.evals.compression_planning import confirmation_plan
from cutback.evals.datasets import digest
from cutback.evals.experiments import ExperimentPlan, plan_summary, run
from cutback.evals.input_groups import effective_input_groups
from cutback.server.plan_artifacts import public_config


def test_actual_context_comparison_rejects_cheaper_but_wrong_compression():
    plan, dataset = fixture()
    executor, retained = NativeBoundary(), []
    report = run(
        plan,
        dataset,
        executor,
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
        record_case=retained.append,
    )
    assert report["status"] == "COMPLETED" and report["requests"] == 9
    choices = {r["candidate"]["key"]: r for r in report["recommendations"]}
    assert choices["compact"]["compression_automation_eligible"]
    assert not choices["unsafe"]["compression_automation_eligible"]
    assert not choices["unsafe"]["quality_gate_passed"]
    assert retained[0]["compression"]["input_context_bytes"] > 0
    assert "deadline-days" not in json.dumps(public_config(report))
    assert report["compression"]["lineage_preparation_cpu_included"]
    confirmed = confirmation_plan(plan, report, "10000000-0000-0000-0000-000000000001", plan.budget)
    assert confirmed.split == "test" and len(confirmed.candidates) == 1
    assert len(confirmed.compression_execution.variants) == 2


@pytest.mark.parametrize("rate,unknown", [(None, False), ("0.01", True)])
def test_unknown_cpu_or_native_usage_cannot_authorize_a_compression_change(rate, unknown):
    plan, dataset = fixture(rate)
    report = run(
        plan,
        dataset,
        NativeBoundary(unknown),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert not any(r.get("compression_automation_eligible") for r in report["recommendations"])
    if unknown:
        assert report["status"] == "FAILED" and report["requests"] == 1
    else:
        assert report["billing_complete"] and report["known_spend_subtotal_usd"] is not None


def test_line_selection_preserves_exact_text_query_and_order_and_never_truncates():
    value = ContextInput(
        query="\u03a9 deadline",
        context="unrelated long line\n\u03a9 deadline=14\n\u03a9 deadline=14\nfinal\n",
    )
    result = compress_context(value, CompressionSettings(mode="query_lines", max_context_bytes=64))
    assert result.query == value.query and "\u03a9 deadline=14\n" in result.context
    assert result.context.count("\u03a9 deadline=14") == 1
    assert result.input_context_bytes > result.output_context_bytes
    value = ContextInput(query="fact", context="fact " + "x" * 100)
    assert (
        compress_context(
            value, CompressionSettings(mode="query_lines", max_context_bytes=64)
        ).context
        == ""
    )


@pytest.mark.parametrize(
    "text",
    [
        '{"query":"x","context":"y","context":"z"}',
        '{"query":"x","context":"y","gold":"secret"}',
        "[]",
        '{"query":"","context":"x"}',
    ],
)
def test_invalid_or_ambiguous_contexts_are_refused(text):
    with pytest.raises(ValueError, match="COMPRESSION_REQUIRES_BOUNDED_QUERY_CONTEXT_JSON"):
        ContextInput.parse(text)


def test_compressed_collisions_across_frozen_splits_block_before_any_request():
    plan, dataset = fixture()
    original = dataset["items"][0]
    raw = json.loads(original["case"]["input"])
    # Distinct raw contexts become exactly equal after duplicate-line removal.
    duplicate = {
        **original,
        "group_hash": digest("other"),
        "split": "test",
        "case": {
            **original["case"],
            "id": "different",
            "input": json.dumps({**raw, "context": raw["context"] + "background notes\n"}),
        },
    }
    dataset["items"].append(duplicate)
    with pytest.raises(ValueError, match="NATIVE_INPUT_FAMILY_SPANS_DATASET_SPLITS"):
        effective_input_groups(plan, dataset)


@pytest.mark.parametrize(
    "field,value", [("content_nonce", None), ("evaluator_version", "deterministic-v3")]
)
def test_compression_cannot_reuse_an_earlier_approval_contract(field, value):
    plan, _ = fixture()
    raw = plan.model_dump(mode="json")
    raw[field] = value
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(raw)


def test_legacy_plan_bytes_still_omit_compression_and_keep_the_same_hash():
    plan, _ = fixture()
    raw = plan.model_dump(mode="json")
    raw.pop("compression_execution")
    raw["evaluator_version"] = "deterministic-v3"
    legacy = ExperimentPlan.model_validate(raw)
    assert "compression_execution" not in legacy.model_dump(mode="json")
    assert digest(legacy.model_dump(mode="json")) == digest(raw)
