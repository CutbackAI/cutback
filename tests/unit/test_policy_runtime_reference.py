"""Observed activation must stay separate from source configuration and approval."""

from datetime import timedelta

import pytest
from support_policy_activity import frame

from cutback.core.policy_activity import PolicyRuntimeSummary
from cutback.core.policy_producer import ASYNC_PROGRAM_SHA256
from cutback.core.policy_runtime_reference import PolicyRuntimeReference, freeze_reference


@pytest.mark.parametrize("active", [False, True])
def test_reference_preserves_measured_scope_and_never_claims_invoice_quality_or_population_coverage(
    active,
):
    _, source, summary, now = frame(active=active)
    result = freeze_reference(source, summary, now=now)
    assert result.cache_requested is active
    assert result.require_retained(summary) is result
    assert result.summary.invoice_verified_cost_usd is result.summary.business_task_success is None
    assert result.summary.capture_coverage == "unknown" and not result.summary.execution_authorized
    assert result.expires_at == summary.scope.window_start + timedelta(hours=1)
    assert "OWNED_PRIVATE_BASE_INSTRUCTION" not in result.model_dump_json()
    assert PolicyRuntimeReference.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "change",
    [
        {"requested_cache_operations": 1},
        {"configured_operations": 1, "requested_cache_operations": 0},
        {"states": {"unknown": 2}},
        {"completions": {"completed": 1, "failed": 1}},
        {"known_native_sdk_invocations": 0, "native_sdk_invocations": 0},
        {"known_cache_operations": 1, "cache_operations": 1},
        {
            "truncated": True,
            "native_sdk_invocations": None,
            "cache_operations": None,
            "cache_component_upper_usd": None,
        },
        {
            "unknown_native_operations": 1,
            "known_native_sdk_invocations": 0,
            "native_sdk_invocations": None,
        },
    ],
)
def test_mixed_missing_failed_or_false_activation_is_refused(change):
    _, source, summary, now = frame()
    raw = {**summary.model_dump(mode="json"), **change}
    changed = PolicyRuntimeSummary.model_validate(raw)
    with pytest.raises(ValueError):
        freeze_reference(source, changed, now=now)


def test_reference_cannot_outlive_raw_activity_digest_source_or_bounded_freshness():
    _, source, summary, now = frame()
    result = freeze_reference(source, summary, now=now)
    with pytest.raises(ValueError, match="RAW_ACTIVITY_CHANGED_OR_EXPIRED"):
        result.require_retained(summary.model_copy(update={"raw_sha256": "f" * 64}))
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        result.require_current(source.model_copy(update={"commit": "f" * 40}), now=now)
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        result.require_current(source, now=result.expires_at)
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        result.require_current(source, now=summary.scope.window_end - timedelta(seconds=1))
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        result.require_current(source, now=now.replace(tzinfo=None))
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        freeze_reference(
            source.model_copy(update={"program_sha256": ASYNC_PROGRAM_SHA256}), summary, now=now
        )


def test_mutated_frozen_summary_cannot_keep_the_old_reference_hash():
    _, source, summary, now = frame()
    result = freeze_reference(source, summary, now=now).model_dump(mode="json")
    result["summary"]["raw_sha256"] = "f" * 64
    with pytest.raises(ValueError):
        PolicyRuntimeReference.model_validate(result)


def test_recent_query_end_cannot_refresh_old_observations():
    _, source, summary, now = frame()
    summary = summary.model_copy(
        update={"scope": summary.scope.model_copy(update={"window_start": now - timedelta(days=1)})}
    )
    with pytest.raises(ValueError, match="CURRENT_SOURCE_AND_FRESH_WINDOW"):
        freeze_reference(source, summary, now=now)
