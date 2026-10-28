from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from cutback.core.task_outcomes import (
    NativeTaskCostEvidence,
    TaskCostComponent,
    TaskOutcomeBatch,
    TaskOutcomeInput,
    summarize_task_economics,
    task_economics,
)


def task(**changes):
    return TaskOutcomeInput(
        task_id=uuid4(),
        result_id=uuid4(),
        environment="staging",
        trace_id=uuid4().hex,
        workflow="synthetic.checkout",
        service="synthetic-shop",
        occurred_at=datetime.now(UTC),
        outcome="success",
        source="owned-app",
        source_version="synthetic-v1",
        basis="business_event",
        **changes,
    )


def evidence(amount, **changes):
    return NativeTaskCostEvidence(
        observed_attempts=1,
        priced_attempts=1,
        known_modeled_subtotal_usd=Decimal(amount),
        trace_retained=True,
        trace_integrity_complete=True,
        **changes,
    )


def test_failed_tasks_are_included_in_cost_per_success_without_billing_or_quality_promotion():
    successful = task_economics(
        task(expected_native_attempts=1, other_costs_declared_complete=True), evidence("1")
    )
    failed_task = task().model_copy(
        update={
            "outcome": "failure",
            "expected_native_attempts": 1,
            "other_costs_declared_complete": True,
        }
    )
    failed = task_economics(failed_task, evidence("2"))
    summary = summarize_task_economics([successful, failed], expected_tasks=2)
    assert Decimal(summary["declared_sample_cost_per_success_usd"]) == Decimal(3)
    assert Decimal(summary["known_failed_task_native_modeled_subtotal_usd"]) == Decimal(2)
    assert summary["declared_successes"] == summary["declared_failures"] == 1
    assert summary["window_cost_complete_under_declaration"]
    assert summary["billing_verification"] == "not_independently_verified"
    assert summary["production_savings_usd"] is None and summary["population_coverage"] == "unknown"


def test_missing_native_usage_or_non_model_components_never_become_free_tasks():
    body = task(expected_native_attempts=2, other_costs_declared_complete=True)
    partial = NativeTaskCostEvidence(
        observed_attempts=2,
        priced_attempts=1,
        known_modeled_subtotal_usd="0.25",
        trace_retained=True,
        trace_integrity_complete=True,
    )
    result = task_economics(body, partial)
    assert not result["declared_cost_complete"] and result["declared_modeled_total_usd"] is None
    assert result["known_native_modeled_subtotal_usd"] == "0.25"
    body = task(
        expected_native_attempts=1,
        other_costs_declared_complete=True,
        components=[TaskCostComponent(key="search", kind="retrieval")],
    )
    result = task_economics(body, evidence("0"))
    assert result["known_other_asserted_subtotal_usd"] is None
    assert (
        result["unknown_declared_components"] == 1 and result["declared_modeled_total_usd"] is None
    )


def test_known_zero_is_distinct_from_no_observation_or_missing_trace():
    body = task(expected_native_attempts=0, other_costs_declared_complete=True)
    missing = NativeTaskCostEvidence(observed_attempts=0, priced_attempts=0)
    assert task_economics(body, missing)["declared_modeled_total_usd"] is None
    retained = missing.model_copy(update={"trace_retained": True, "trace_integrity_complete": True})
    result = task_economics(body, retained)
    assert Decimal(result["declared_modeled_total_usd"]) == 0
    assert result["known_native_modeled_subtotal_usd"] is None
    assert result["billing_verification"] == "not_independently_verified"


def test_excluded_evaluation_or_optimization_traffic_blocks_complete_application_cost():
    body = task(expected_native_attempts=1, other_costs_declared_complete=True)
    result = task_economics(body, evidence("0.1", excluded_non_application_attempts=1))
    assert result["native_attempt_count_matches_declaration"] is True
    assert result["declared_modeled_total_usd"] is None


def test_unknown_outcomes_and_missing_task_denominators_prevent_window_claims():
    body = task(expected_native_attempts=1, other_costs_declared_complete=True)
    known = task_economics(body, evidence("1"))
    unknown = task_economics(
        body.model_copy(update={"task_id": uuid4(), "outcome": "unknown"}), evidence("2")
    )
    assert (
        summarize_task_economics([known, unknown])["declared_sample_cost_per_success_usd"] is None
    )
    missing_task = summarize_task_economics([known], expected_tasks=2)
    assert not missing_task["window_cost_complete_under_declaration"]
    assert missing_task["declared_window_cost_per_success_usd"] is None
    with pytest.raises(ValueError, match="DOUBLE_COUNT"):
        summarize_task_economics([known, known])
    empty = summarize_task_economics([])
    assert empty["known_declared_complete_task_subtotal_usd"] is None
    assert empty["known_failed_task_native_modeled_subtotal_usd"] is None


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0.0000000000001"])
def test_component_money_rejects_invalid_or_unrepresentable_amounts(amount):
    with pytest.raises(ValueError):
        TaskCostComponent(key="external-tool", kind="tool", amount_usd=amount)


def test_task_contract_rejects_raw_content_duplicate_keys_and_stale_batch():
    raw = task().model_dump(mode="json")
    with pytest.raises(ValueError):
        TaskOutcomeInput.model_validate({**raw, "explanation": "raw customer content is forbidden"})
    with pytest.raises(ValueError, match="UNIQUE"):
        task(components=[{"key": "same", "kind": "tool"}, {"key": "same", "kind": "other"}])
    old = task().model_copy(update={"occurred_at": datetime.now(UTC) - timedelta(days=91)})
    with pytest.raises(ValueError, match="INGEST_WINDOW"):
        TaskOutcomeBatch(items=[old])
