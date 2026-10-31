"""Explicit fingerprint capture rights; snapshots belong in encrypted storage."""

import heapq
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from itertools import islice
from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, ConfigDict, Field, field_validator

from cutback.core.models import Identifier, StrictModel

NATIVE_REQUEST_PROFILE_CONTRACT = "native-request-profile-v1"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class NativeRequestProfileAuthorization(StrictModel):
    version: Literal["native-request-profile-v1"] = "native-request-profile-v1"
    freshness_ref: Identifier
    prospective_ttl_seconds: int = Field(strict=True, ge=1, le=2592000)
    fingerprint_capture_authorized: Literal[True]
    immutable_or_safely_invalidated: Literal[True]

    @field_validator(
        "fingerprint_capture_authorized", "immutable_or_safely_invalidated", mode="before"
    )
    @classmethod
    def explicit_rights(cls, value):
        if value is not True:
            raise ValueError("NATIVE_PROFILE_EXPLICIT_BOOLEAN_AUTHORITY_REQUIRED")
        return value


class NativeRequestProfileSnapshot(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    version: Literal["native-request-profile-v1"] = "native-request-profile-v1"
    request_identity_contract: Literal["native-exact-cache-v1"] = "native-exact-cache-v1"
    origin: Literal["client_reported_sdk_capture"] = "client_reported_sdk_capture"
    capture_policy_sha256: Digest
    profile_key_id: UUID4
    clock_domain_id: UUID4
    monotonic_started_ns: int = Field(strict=True, ge=0, le=9223372036854775807)
    request_hmac_sha256: Digest
    sdk_version: Literal["2.29.0", "3.35.1"]
    response_cache_eligible: bool = Field(strict=True)


class NativeProfileObservation(StrictModel):
    event_id: UUID
    occurred_at: datetime
    duration_ms: float | None = Field(default=None, strict=True, ge=0, le=86400000)
    profile: NativeRequestProfileSnapshot
    provider_modeled_cost_usd: Decimal | None = Field(default=None, ge=0)

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("NATIVE_PROFILE_UTC_OFFSET_REQUIRED")
        return value.astimezone(UTC)

    @field_validator("provider_modeled_cost_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {Decimal, str, int}:
            raise ValueError("NATIVE_PROFILE_COST_REQUIRES_EXACT_DECIMAL")
        return value


def summarize_native_profile(
    observations,
    authorization: NativeRequestProfileAuthorization,
    *,
    capture_policy_sha256: str,
    metadata_attempts: int | None,
    sample_rate: float,
    truncated: bool = False,
):
    """Model potential reuse in a retained stream; never claim actual delivery.

    Each key starts cold. A successful cold response can fill the hypothetical
    cache at its observed completion, with zero cache transport latency. Parallel
    cold calls remain misses. Equality at a completion boundary is ambiguous.
    Sampled/missing/truncated streams cannot authorize automatic cache selection.
    """
    records = [NativeProfileObservation.model_validate(item) for item in islice(observations, 1001)]
    if len(records) > 1000 or not 0 <= sample_rate <= 1:
        raise ValueError("NATIVE_PROFILE_ANALYSIS_BOUND_REQUIRED")
    if metadata_attempts is not None and (
        type(metadata_attempts) is not int or not 0 <= metadata_attempts <= 100000
    ):
        raise ValueError("NATIVE_PROFILE_METADATA_COUNT_BOUND_REQUIRED")
    if any(item.profile.capture_policy_sha256 != capture_policy_sha256 for item in records):
        raise ValueError("NATIVE_PROFILE_POLICY_SCOPE_MISMATCH")
    by_event: dict[UUID, NativeProfileObservation] = {}
    conflicts = 0
    for item in records:
        if item.event_id in by_event and by_event[item.event_id] != item:
            conflicts += 1
        else:
            by_event[item.event_id] = item
    records = sorted(
        by_event.values(),
        key=lambda item: (
            item.profile.clock_domain_id.int,
            item.profile.monotonic_started_ns,
            item.event_id.int,
        ),
    )
    if metadata_attempts is not None and metadata_attempts < len(records):
        raise ValueError("NATIVE_PROFILE_METADATA_COUNT_CONFLICT")
    missing = None if metadata_attempts is None else metadata_attempts - len(records)
    pending: dict[tuple, list[int]] = {}
    expires: dict[tuple, int] = {}
    keys = set()
    clock_domains = set()
    hits = overlaps = ambiguous = unknown_timing = ineligible = 0
    avoided = Decimal(0)
    avoided_unknown = False
    ttl = authorization.prospective_ttl_seconds * 1000000000
    for item in records:
        profile = item.profile
        clock_domains.add(profile.clock_domain_id)
        key = (
            profile.clock_domain_id,
            profile.profile_key_id,
            profile.sdk_version,
            profile.request_hmac_sha256,
        )
        keys.add(key)
        start = profile.monotonic_started_ns
        completions = pending.setdefault(key, [])
        while completions and completions[0] < start:
            completed = heapq.heappop(completions)
            if expires.get(key, -1) <= completed:
                expires[key] = completed + ttl
        boundary = bool(completions and completions[0] == start)
        if boundary:
            ambiguous += 1
        if not profile.response_cache_eligible:
            ineligible += 1
        if item.duration_ms is None:
            unknown_timing += 1
        if expires.get(key, -1) > start and not boundary:
            hits += 1
            if item.provider_modeled_cost_usd is None:
                avoided_unknown = True
            else:
                avoided += item.provider_modeled_cost_usd
            continue
        if completions:
            overlaps += 1
        if profile.response_cache_eligible and item.duration_ms is not None:
            duration = int(
                (Decimal(str(item.duration_ms)) * 1000000).to_integral_value(rounding=ROUND_CEILING)
            )
            heapq.heappush(completions, start + duration)
    reasons = []
    for condition, reason in (
        (not records, "NO_RETAINED_PROFILES"),
        (metadata_attempts is None, "METADATA_DENOMINATOR_UNKNOWN"),
        (bool(missing), "MISSING_PROFILED_ATTEMPTS"),
        (sample_rate != 1, "SAMPLED_STREAM_CANNOT_MODEL_CACHE_CENSUS"),
        (truncated, "RETAINED_WINDOW_TRUNCATED"),
        (conflicts > 0, "CONFLICTING_EVENT_PROFILES"),
        (len(clock_domains) > 1, "MULTIPLE_UNSYNCHRONIZED_CLOCK_DOMAINS"),
        (unknown_timing > 0, "NATIVE_COMPLETION_TIMING_UNKNOWN"),
        (ineligible > 0, "UNQUALIFIED_NATIVE_RESPONSES"),
        (ambiguous > 0, "COMPLETION_BOUNDARY_AMBIGUOUS"),
        (hits == 0, "NO_MODELED_REPEAT_WITHIN_TTL"),
    ):
        if condition:
            reasons.append(reason)
    return {
        "version": NATIVE_REQUEST_PROFILE_CONTRACT,
        "scope": "retained_client_reported_sdk_stream",
        "timing_model": "monotonic_cold_start_completion_ttl_zero_cache_transport_latency",
        "clock_domains": len(clock_domains),
        "prospective_ttl_seconds": authorization.prospective_ttl_seconds,
        "profiled_attempts": len(records),
        "metadata_attempts": metadata_attempts,
        "missing_profiled_attempts": missing,
        "distinct_request_identities": len(keys),
        "modeled_potential_hits": hits,
        "modeled_cold_misses": len(records) - hits,
        "overlapping_cold_misses": overlaps,
        "ambiguous_completion_boundaries": ambiguous,
        "unknown_native_completion_times": unknown_timing,
        "unqualified_native_responses": ineligible,
        "cache_trial_eligible": not reasons,
        "abstention_reasons": reasons,
        "modeled_avoided_provider_cost_usd": None
        if avoided_unknown or not records
        else str(avoided),
        "cache_infrastructure_cost_usd": None,
        "whole_task_cost_usd": None,
        "invoice_verified_cost_usd": None,
        "production_hit_rate": None,
        "realized_savings_usd": None,
        "semantic_quality": "not_evaluated",
        "cache_delivery_benchmarked": False,
    }
