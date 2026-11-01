"""Content-free fixed-policy activity; cache hits are never new provider attempts."""

from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from cutback.core.models import Identifier, StrictModel

Nano = Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]


class PolicyRuntimeEvent(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.source-policy-runtime.v1"] = "cutback.source-policy-runtime.v1"
    event_id: UUID
    environment: Identifier
    service_name: Identifier
    workflow: Identifier
    call_site_id: Identifier
    source_commit: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    started_ns: Nano
    ended_ns: Nano
    cache_configured: StrictBool
    cache_requested: StrictBool
    state: Literal["inactive", "native_miss", "hit", "unknown"]
    completion: Literal["completed", "failed", "cancelled"]
    native_sdk_invocations: Annotated[StrictInt, Field(ge=0, le=1)] | None
    cache_operations: Annotated[StrictInt, Field(ge=0, le=2)] | None
    cache_write: (
        Literal[
            "not_applicable",
            "not_attempted",
            "created",
            "conflict",
            "rejected",
            "uncertain",
            "not_eligible",
        ]
        | None
    )
    usage_origin: Literal["native_response", "historical_cached_response"] | None
    cache_operation_upper_usd: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=12)
    cache_cost_basis_ref: Identifier | None = None
    invoice_verified_cost_usd: None = None
    business_task_success: None = None

    @field_validator("cache_operation_upper_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_ACTIVITY_EXACT_DECIMAL_REQUIRED")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if (
            not self.event_id.int
            or self.call_site_id == "unattributed"
            or self.ended_ns < self.started_ns
            or self.cache_requested
            and not self.cache_configured
            or self.cache_configured != (self.cache_operation_upper_usd is not None)
            or self.cache_configured != (self.cache_cost_basis_ref is not None)
        ):
            raise ValueError("POLICY_ACTIVITY_COMPLETE_SOURCE_AND_CACHE_FRAME_REQUIRED")
        if self.completion != "completed":
            if self.cache_requested:
                valid = (
                    self.state == "unknown"
                    and self.native_sdk_invocations is None
                    and self.cache_operations is None
                    and self.cache_write is None
                    and self.usage_origin is None
                )
            else:
                valid = (
                    self.state == "inactive"
                    and self.native_sdk_invocations == 1
                    and self.cache_operations == 0
                    and self.cache_write == "not_applicable"
                    and self.usage_origin is None
                )
        elif not self.cache_requested:
            valid = (
                self.state == "inactive"
                and self.native_sdk_invocations == 1
                and self.cache_operations == 0
                and self.cache_write == "not_applicable"
                and self.usage_origin == "native_response"
            )
        elif self.state == "hit":
            valid = (
                self.native_sdk_invocations == 0
                and self.cache_operations == 1
                and self.cache_write == "not_attempted"
                and self.usage_origin == "historical_cached_response"
            )
        else:
            valid = (
                self.state == "native_miss"
                and self.native_sdk_invocations == 1
                and self.cache_write
                in {"created", "conflict", "rejected", "uncertain", "not_eligible"}
                and self.cache_operations == (1 if self.cache_write == "not_eligible" else 2)
                and self.usage_origin == "native_response"
            )
        if not valid:
            raise ValueError("POLICY_ACTIVITY_COUNTS_OR_ORIGINS_INCONSISTENT")
        return self

    @property
    def cache_component_upper_usd(self):
        if self.cache_operations is None:
            return None
        if self.cache_operations == 0:
            return Decimal(0)
        if self.cache_operation_upper_usd is None:
            return None
        with localcontext(Context(prec=64)):
            return self.cache_operations * self.cache_operation_upper_usd


class PolicyRuntimeBatch(StrictModel):
    version: Literal["cutback.source-policy-runtime-batch.v1"] = (
        "cutback.source-policy-runtime-batch.v1"
    )
    project_id: UUID
    events: list[PolicyRuntimeEvent] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bounded_fresh_events(self):
        now = datetime.now(UTC)
        earliest = int((now - timedelta(days=30)).timestamp()) * 1_000_000_000
        latest = int((now + timedelta(minutes=5)).timestamp()) * 1_000_000_000
        if not self.project_id.int or any(
            row.started_ns < earliest or row.ended_ns > latest for row in self.events
        ):
            raise ValueError("POLICY_ACTIVITY_PROJECT_OR_INGEST_WINDOW_INVALID")
        return self


class PolicyRuntimeScope(StrictModel):
    environment: Identifier
    service_name: Identifier
    workflow: Identifier
    call_site_id: Identifier
    source_commit: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    window_start: AwareDatetime
    window_end: AwareDatetime

    @model_validator(mode="after")
    def bounded_window(self):
        if not timedelta(0) < self.window_end - self.window_start <= timedelta(days=30):
            raise ValueError("POLICY_ACTIVITY_WINDOW_MUST_BE_POSITIVE_AND_AT_MOST_30_DAYS")
        if self.call_site_id == "unattributed":
            raise ValueError("POLICY_ACTIVITY_EXACT_CALL_SITE_REQUIRED")
        if (
            not datetime(1970, 1, 1, tzinfo=UTC)
            <= self.window_start
            < self.window_end
            < datetime(2262, 1, 1, tzinfo=UTC)
        ):
            raise ValueError("POLICY_ACTIVITY_WINDOW_OUTSIDE_DATABASE_NANOSECOND_RANGE")
        return self


class PolicyRuntimeSummary(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    profile: Literal["cutback.source-policy-runtime-summary.v1"] = (
        "cutback.source-policy-runtime-summary.v1"
    )
    scope: PolicyRuntimeScope
    project_id: UUID
    environment_id: UUID
    sample_limit: Literal[10000] = 10000
    retained_operations: Annotated[StrictInt, Field(ge=0, le=10000)]
    truncated: StrictBool
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    states: dict[
        Literal["hit", "native_miss", "inactive", "unknown"],
        Annotated[StrictInt, Field(ge=0, le=10000)],
    ]
    completions: dict[
        Literal["completed", "failed", "cancelled"], Annotated[StrictInt, Field(ge=0, le=10000)]
    ]
    configured_operations: Annotated[StrictInt, Field(ge=0, le=10000)]
    requested_cache_operations: Annotated[StrictInt, Field(ge=0, le=10000)]
    known_native_sdk_invocations: Annotated[StrictInt, Field(ge=0, le=10000)]
    native_sdk_invocations: Annotated[StrictInt, Field(ge=0, le=10000)] | None
    unknown_native_operations: Annotated[StrictInt, Field(ge=0, le=10000)]
    known_cache_operations: Annotated[StrictInt, Field(ge=0, le=20000)]
    cache_operations: Annotated[StrictInt, Field(ge=0, le=20000)] | None
    unknown_cache_operations: Annotated[StrictInt, Field(ge=0, le=10000)]
    cache_component_upper_usd: Decimal | None = Field(ge=0, le=2000000, decimal_places=12)
    cache_cost_basis_refs: list[Identifier] = Field(max_length=10000)
    capture_coverage: Literal["unknown"] = "unknown"
    identity_basis: Literal["client_reported_source_map"] = "client_reported_source_map"
    invoice_verified_cost_usd: None = None
    business_task_success: None = None
    content_included: Literal[False] = False
    execution_authorized: Literal[False] = False

    @field_validator("cache_component_upper_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_ACTIVITY_EXACT_DECIMAL_REQUIRED")
        return value

    @model_validator(mode="after")
    def consistent_summary(self):
        count = self.retained_operations
        incomplete = not count or self.truncated
        if (
            not self.project_id.int
            or not self.environment_id.int
            or sum(self.states.values()) != count
            or sum(self.completions.values()) != count
            or not 0 <= self.requested_cache_operations <= self.configured_operations <= count
            or not 0 <= self.known_native_sdk_invocations <= count - self.unknown_native_operations
            or not 0 <= self.unknown_cache_operations <= count
            or self.known_cache_operations > 2 * (count - self.unknown_cache_operations)
            or self.native_sdk_invocations
            != (
                None
                if self.unknown_native_operations or incomplete
                else self.known_native_sdk_invocations
            )
            or self.cache_operations
            != (
                None if self.unknown_cache_operations or incomplete else self.known_cache_operations
            )
            or (self.cache_component_upper_usd is None)
            != bool(self.unknown_cache_operations or incomplete)
        ):
            raise ValueError("POLICY_ACTIVITY_INCONSISTENT_OR_FALSE_COMPLETE_SUMMARY")
        return self
