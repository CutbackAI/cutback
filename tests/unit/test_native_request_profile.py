"""Fingerprinting cannot silently become default telemetry or inferred permission."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import repeat
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.core.native_request_profile import (
    NativeRequestProfileAuthorization,
    NativeRequestProfileSnapshot,
    summarize_native_profile,
)


def authorization():
    return {
        "freshness_ref": "owned-immutable-window",
        "prospective_ttl_seconds": 300,
        "fingerprint_capture_authorized": True,
        "immutable_or_safely_invalidated": True,
    }


@pytest.mark.parametrize(
    "field", ["fingerprint_capture_authorized", "immutable_or_safely_invalidated"]
)
@pytest.mark.parametrize("value", [1, "true", False, None])
def test_profile_rights_are_explicit_booleans(field, value):
    with pytest.raises(ValidationError):
        NativeRequestProfileAuthorization.model_validate({**authorization(), field: value})


@pytest.mark.parametrize("ttl", [0, 2592001, True, 1.5])
def test_profile_window_is_finite_and_exact(ttl):
    with pytest.raises(ValidationError):
        NativeRequestProfileAuthorization.model_validate(
            {**authorization(), "prospective_ttl_seconds": ttl}
        )


@pytest.mark.parametrize("field", ["input", "output", "headers", "api_key", "request"])
def test_snapshot_has_no_arbitrary_content_or_credential_fields(field):
    body = {
        "capture_policy_sha256": "a" * 64,
        "profile_key_id": str(uuid4()),
        "clock_domain_id": str(uuid4()),
        "monotonic_started_ns": 1000,
        "request_hmac_sha256": "b" * 64,
        "sdk_version": "2.29.0",
        "response_cache_eligible": True,
        field: "PRIVATE_SYNTHETIC_FORBIDDEN",
    }
    with pytest.raises(ValidationError) as failure:
        NativeRequestProfileSnapshot.model_validate(body)
    assert "PRIVATE_SYNTHETIC_FORBIDDEN" not in str(failure.value)


def observation(at, *, duration=100, key="b", key_id=None, eligible=True, cost="0.01"):
    return {
        "event_id": uuid4(),
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=at),
        "duration_ms": duration,
        "provider_modeled_cost_usd": cost,
        "profile": {
            "capture_policy_sha256": "a" * 64,
            "profile_key_id": key_id or PROFILE_KEY_ID,
            "clock_domain_id": PROFILE_CLOCK_DOMAIN_ID,
            "monotonic_started_ns": int(Decimal(str(at)) * 1000000000),
            "request_hmac_sha256": key * 64,
            "sdk_version": "2.29.0",
            "response_cache_eligible": eligible,
        },
    }


PROFILE_KEY_ID = uuid4()
PROFILE_CLOCK_DOMAIN_ID = uuid4()


def summary(rows, **options):
    return summarize_native_profile(
        rows,
        NativeRequestProfileAuthorization.model_validate(authorization()),
        capture_policy_sha256="a" * 64,
        metadata_attempts=options.pop("metadata_attempts", len(rows)),
        sample_rate=options.pop("sample_rate", 1),
        **options,
    )


def test_temporal_model_starts_cold_and_counts_only_repeats_before_completion_based_expiry():
    result = summary([observation(0), observation(1), observation(300.1), observation(301)])
    assert result["modeled_potential_hits"] == 2
    assert result["modeled_cold_misses"] == 2
    assert result["modeled_avoided_provider_cost_usd"] == "0.02"
    assert result["cache_trial_eligible"] is True
    assert result["production_hit_rate"] is None
    assert result["realized_savings_usd"] is None
    assert "b" * 64 not in str(result) and str(PROFILE_KEY_ID) not in str(result)


def test_parallel_cold_requests_do_not_become_hits_or_refresh_a_live_cache():
    rows = [observation(0, duration=2000), observation(1, duration=4000), observation(3)]
    result = summary(rows + [observation(303), observation(304)])
    assert result["modeled_potential_hits"] == 2
    assert result["overlapping_cold_misses"] == 1
    # The completion at t=5 must not refresh the entry filled at t=2.
    assert result["modeled_cold_misses"] == 3


def test_key_rotation_and_request_changes_never_join_identities():
    result = summary([observation(0), observation(1, key_id=uuid4()), observation(2, key="c")])
    assert result["distinct_request_identities"] == 3
    assert result["modeled_potential_hits"] == 0
    assert result["cache_trial_eligible"] is False


@pytest.mark.parametrize(
    "options,reason",
    [
        ({"sample_rate": 0.5}, "SAMPLED_STREAM_CANNOT_MODEL_CACHE_CENSUS"),
        ({"metadata_attempts": None}, "METADATA_DENOMINATOR_UNKNOWN"),
        ({"metadata_attempts": 3}, "MISSING_PROFILED_ATTEMPTS"),
        ({"truncated": True}, "RETAINED_WINDOW_TRUNCATED"),
    ],
)
def test_incomplete_stream_never_authorizes_selection(options, reason):
    result = summary([observation(0), observation(1)], **options)
    assert result["cache_trial_eligible"] is False
    assert reason in result["abstention_reasons"]


def test_unknown_completion_and_unqualified_responses_cannot_seed_cache():
    result = summary(
        [observation(0, duration=None), observation(1, eligible=False), observation(2)]
    )
    assert result["modeled_potential_hits"] == 0
    assert result["unknown_native_completion_times"] == 1
    assert result["unqualified_native_responses"] == 1


def test_equal_completion_boundary_abstains_and_unknown_hit_cost_is_not_zero():
    boundary = summary([observation(0, duration=1000), observation(1)])
    assert "COMPLETION_BOUNDARY_AMBIGUOUS" in boundary["abstention_reasons"]
    result = summary([observation(0), observation(1, cost=None)])
    assert result["modeled_potential_hits"] == 1
    assert result["modeled_avoided_provider_cost_usd"] is None


def test_duplicate_events_are_not_double_counted_and_conflicts_abstain():
    first, second = observation(0), observation(1)
    assert summary([second, first, first], metadata_attempts=2)["profiled_attempts"] == 2
    result = summary([first, {**first, "duration_ms": 200}, second], metadata_attempts=2)
    assert "CONFLICTING_EVENT_PROFILES" in result["abstention_reasons"]


def test_analysis_bounds_iterators_and_rejects_invalid_scope_and_float_money():
    auth = NativeRequestProfileAuthorization.model_validate(authorization())
    with pytest.raises(ValueError, match="BOUND"):
        summarize_native_profile(
            repeat(observation(0)),
            auth,
            capture_policy_sha256="a" * 64,
            metadata_attempts=None,
            sample_rate=1,
        )
    with pytest.raises(ValueError, match="SCOPE"):
        summarize_native_profile(
            [observation(0)],
            auth,
            capture_policy_sha256="c" * 64,
            metadata_attempts=1,
            sample_rate=1,
        )
    with pytest.raises(ValueError, match="COUNT_CONFLICT"):
        summary([observation(0)], metadata_attempts=0)
    with pytest.raises(ValidationError):
        summary([observation(0, cost=0.01)])
    assert (
        summary([observation(0), observation(1, cost=Decimal("0.00000000001"))])[
            "modeled_avoided_provider_cost_usd"
        ]
        == "1E-11"
    )


def test_unsynchronized_processes_never_join_or_authorize_automatic_cache_selection():
    first, second = observation(0), observation(1)
    second["profile"]["clock_domain_id"] = uuid4()
    result = summary([first, second])
    assert result["clock_domains"] == 2
    assert result["modeled_potential_hits"] == 0
    assert "MULTIPLE_UNSYNCHRONIZED_CLOCK_DOMAINS" in result["abstention_reasons"]


def test_wall_clock_adjustment_does_not_change_monotonic_reuse_model():
    first, second = observation(0), observation(1)
    second["occurred_at"] -= timedelta(days=1)
    assert summary([first, second])["modeled_potential_hits"] == 1
