"""Activity metadata preserves unknown charges and separates configuration from activation."""

import time
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.core.policy_activity import PolicyRuntimeBatch, PolicyRuntimeEvent


def body(**changes):
    start = time.time_ns()
    return {
        "event_id": str(uuid4()),
        "environment": "staging",
        "service_name": "owned-desk",
        "workflow": "owned-classify",
        "call_site_id": "site:" + "a" * 24,
        "source_commit": "b" * 40,
        "policy_sha256": "c" * 64,
        "started_ns": start,
        "ended_ns": start + 1000,
        "cache_configured": True,
        "cache_requested": True,
        "state": "hit",
        "completion": "completed",
        "native_sdk_invocations": 0,
        "cache_operations": 1,
        "cache_write": "not_attempted",
        "usage_origin": "historical_cached_response",
        "cache_operation_upper_usd": "0.000000123456",
        "cache_cost_basis_ref": "owned-upper",
        **changes,
    }


def test_hits_never_turn_historical_usage_into_new_native_invocations_or_invoice_cost():
    event = PolicyRuntimeEvent.model_validate(body())
    assert event.native_sdk_invocations == 0
    assert event.usage_origin == "historical_cached_response"
    assert event.invoice_verified_cost_usd is event.business_task_success is None
    with localcontext() as context:
        context.prec = 1
        assert event.cache_component_upper_usd == Decimal("0.000000123456")


def test_configured_cache_without_runtime_scope_is_explicitly_inactive():
    event = PolicyRuntimeEvent.model_validate(
        body(
            cache_requested=False,
            state="inactive",
            native_sdk_invocations=1,
            cache_operations=0,
            cache_write="not_applicable",
            usage_origin="native_response",
        )
    )
    assert event.cache_configured and not event.cache_requested
    assert event.cache_component_upper_usd == 0 and event.invoice_verified_cost_usd is None


@pytest.mark.parametrize("completion", ["failed", "cancelled"])
def test_failed_active_cache_operations_preserve_unknown_call_and_operation_counts(completion):
    event = PolicyRuntimeEvent.model_validate(
        body(
            completion=completion,
            state="unknown",
            native_sdk_invocations=None,
            cache_operations=None,
            cache_write=None,
            usage_origin=None,
        )
    )
    assert event.native_sdk_invocations is event.cache_component_upper_usd is None


@pytest.mark.parametrize(
    "changes",
    [
        {"native_sdk_invocations": 1},
        {"cache_operations": 2},
        {"cache_requested": False},
        {"cache_configured": False},
        {"cache_operation_upper_usd": 0.1},
        {"native_sdk_invocations": False},
        {"invoice_verified_cost_usd": "0"},
        {"business_task_success": True},
        {"call_site_id": "unattributed"},
        {"source_commit": "unknown"},
        {"response": "SYNTHETIC_PRIVATE_CONTENT"},
        {"token": "SYNTHETIC_PRIVATE_KEY"},
    ],
)
def test_incoherent_or_payload_bearing_metadata_is_refused(changes):
    with pytest.raises(ValidationError):
        PolicyRuntimeEvent.model_validate(body(**changes))


@pytest.mark.parametrize(
    "write,operations", [("created", 2), ("uncertain", 2), ("not_eligible", 1)]
)
def test_cache_miss_bounds_include_attempted_writes_even_when_acknowledgement_is_unknown(
    write, operations
):
    event = PolicyRuntimeEvent.model_validate(
        body(
            state="native_miss",
            native_sdk_invocations=1,
            cache_operations=operations,
            cache_write=write,
            usage_origin="native_response",
        )
    )
    assert event.cache_component_upper_usd == Decimal("0.000000123456") * operations
    assert event.invoice_verified_cost_usd is None


def test_event_batches_require_a_real_project_and_bounded_current_timestamps():
    batch = PolicyRuntimeBatch(
        project_id=uuid4(), events=[PolicyRuntimeEvent.model_validate(body())]
    )
    assert len(batch.events) == 1
    with pytest.raises(ValidationError):
        PolicyRuntimeBatch(
            project_id=uuid4(),
            events=[PolicyRuntimeEvent.model_validate(body(started_ns=1, ended_ns=2))],
        )
