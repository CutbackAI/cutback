"""Synthetic component evidence; model-only failure cannot masquerade as a joint win."""

import json
from copy import deepcopy
from decimal import Decimal

import pytest
from support_compression import fixture

from cutback.core.compression import CompressionSettings
from cutback.evals.compression_planning import confirmation_plan as legacy_confirmation
from cutback.evals.experiments import Budget, Execution, ExperimentPlan, plan_summary, run
from cutback.evals.joint_context_planning import confirmation_plan, draft_joint_plan


def prepared(*, rate="0.01"):
    original, dataset = fixture()
    baseline = original.baseline.model_copy(update={"model": "gpt-4.1"})
    cheap = original.baseline.model_copy(update={"key": "cheaper"})
    settings = original.compression_execution.variants[0].settings
    plan = draft_joint_plan(
        {
            "workflow": original.workflow,
            "hash": dataset["immutable_hash"],
            "provenance": {"independent_labels": True},
        },
        baseline,
        settings,
        models=[cheap],
        contexts=[
            CompressionSettings(mode="deduplicate_lines", max_context_bytes=1024),
            CompressionSettings(mode="query_lines", max_context_bytes=64),
        ],
        metric=original.metric,
        budget=Budget(max_usd="1", max_requests=100),
        assumed_usd_per_cpu_hour=rate,
        transport="scripted_test_only",
    )
    return original, plan, dataset


class OwnedBoundary:
    synthetic_transport = True

    def execute(self, model, text, timeout_seconds):
        assert "evaluator-only-compression-canary" not in text
        frame = json.loads(text[text.index("{") :])
        known = "deadline-days=14" in frame["context"]
        cheap = model.model == "gpt-4.1-mini"
        correct = known and (not cheap or len(frame["context"].splitlines()) <= 3)
        return Execution(
            output="14" if correct else "wrong",
            cost_usd=Decimal(len(text.encode())) / Decimal(10000000 if cheap else 1000000),
            latency_ms=1,
            actual_model=model.model,
        )


def test_frozen_joint_grid_rejects_model_only_and_selects_real_context_and_model_change():
    original, plan, dataset = prepared()
    raw = plan.model_dump(mode="json")
    assert plan.evaluator_version == "deterministic-v12"
    assert len(plan.candidates) == 5
    for change in ("old_evaluator", "old_spec", "instructions", "provider", "output_widen"):
        changed = deepcopy(raw)
        if change == "old_evaluator":
            changed["evaluator_version"] = "deterministic-v7"
        elif change == "old_spec":
            changed["compression_execution"]["version"] = "cutback.compression-execution.v1"
        elif change == "instructions":
            changed["candidates"][0]["instruction"] = "changed task"
        elif change == "provider":
            changed["candidates"][0]["provider"] = "anthropic"
        else:
            changed["candidates"][0]["max_output_tokens"] = 256
        with pytest.raises(ValueError):
            ExperimentPlan.model_validate(changed)
    assert (
        ExperimentPlan.model_validate(original.model_dump()).model_dump_json()
        == original.model_dump_json()
    )
    report = run(
        plan,
        dataset,
        OwnedBoundary(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["status"] == "COMPLETED" and report["requests"] == 18
    rows = report["recommendations"]
    model_only = next(row for row in rows if row["search_axes_changed"] == ["model"])
    assert not model_only["quality_gate_passed"]
    assert not model_only["joint_model_context_confirmation_eligible"]
    joint = next(
        row
        for row in rows
        if row["search_axes_changed"] == ["model", "extractive_context"]
        and row["joint_model_context_confirmation_eligible"]
    )
    assert all(row["compression_automation_eligible"] is False for row in rows)
    assert report["joint_model_context"]["cost_evidence"] == "scripted_fixture_usage"
    assert report["joint_model_context"]["split"] == "validation"
    confirmed = confirmation_plan(plan, report, "10000000-0000-0000-0000-000000000001", plan.budget)
    assert confirmed.candidates[0].key == joint["candidate"]["key"]
    assert len(confirmed.compression_execution.variants) == 2 and confirmed.split == "test"
    with pytest.raises(ValueError):
        legacy_confirmation(plan, report, "10000000-0000-0000-0000-000000000001", plan.budget)


def test_unknown_cpu_cost_does_not_authorize_joint_confirmation():
    _, plan, dataset = prepared(rate=None)
    report = run(
        plan,
        dataset,
        OwnedBoundary(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["status"] == "COMPLETED" and report["billing_complete"]
    assert not any(
        row["joint_model_context_confirmation_eligible"] for row in report["recommendations"]
    )
    with pytest.raises(ValueError):
        confirmation_plan(plan, report, "10000000-0000-0000-0000-000000000001", plan.budget)


def test_uncertain_native_call_stops_joint_search_without_replaying_or_losing_unknown_cost():
    _, plan, dataset = prepared()
    calls = []

    class Outage(OwnedBoundary):
        def execute(self, model, text, timeout_seconds):
            calls.append(model.key)
            raise TimeoutError("OWNED_PRIVATE_NATIVE_OUTAGE")

    report = run(
        plan,
        dataset,
        Outage(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    assert report["status"] == "FAILED" and report["requests"] == len(calls) == 1
    assert not report["billing_complete"]
    assert not any(
        row.get("joint_model_context_confirmation_eligible") for row in report["recommendations"]
    )
    assert "OWNED_PRIVATE_NATIVE_OUTAGE" not in json.dumps(report)


def test_joint_regression_gate_requires_current_hash_confirmation_lineage_and_known_components():
    from cutback.evals.joint_context_planning import regression_gate

    _, plan, dataset = prepared()
    validation = run(
        plan,
        dataset,
        OwnedBoundary(),
        approval_hash=plan_summary(plan, dataset)["plan_hash"],
        independent_labels=True,
    )
    chosen = confirmation_plan(
        plan, validation, "10000000-0000-0000-0000-000000000001", plan.budget
    )
    heldout = {**dataset, "items": [{**value, "split": "test"} for value in dataset["items"]]}
    report = run(
        chosen,
        heldout,
        OwnedBoundary(),
        approval_hash=plan_summary(chosen, heldout)["plan_hash"],
        independent_labels=True,
    )
    # A synthetic unit receipt exercises gating only. Real lineage is server-owned.
    report["lineage"] = {"fresh_test_claim": True}
    frozen = report["plan"]["plan_hash"]
    assert regression_gate(report, expected_plan_hash=frozen, sample_only=True)["passed"]
    changes = [
        ("billing_complete", False),
        ("lineage", {"fresh_test_claim": None}),
        ("joint_model_context", {}),
        (
            "recommendations",
            [
                {**row, "joint_model_context_confirmation_eligible": False}
                for row in report["recommendations"]
            ],
        ),
    ]
    for key, value in changes:
        assert not regression_gate(
            {**report, key: value}, expected_plan_hash=frozen, sample_only=True
        )["passed"]
    assert not regression_gate(report, expected_plan_hash="a" * 64, sample_only=True)["passed"]
    assert not regression_gate(
        validation, expected_plan_hash=validation["plan"]["plan_hash"], sample_only=True
    )["passed"]
