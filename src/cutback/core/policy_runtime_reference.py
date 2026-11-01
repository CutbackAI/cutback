"""Freeze observed cache activation without turning it into cost, quality or authority."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, ConfigDict, Field, StrictBool, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_activity import PolicyRuntimeSummary
from cutback.core.policy_producer import PolicySourceBinding
from cutback.core.request_profiles import fingerprint


class PolicyRuntimeReference(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.source-policy-runtime-reference.v1"] = (
        "cutback.source-policy-runtime-reference.v1"
    )
    source_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: PolicyRuntimeSummary
    summary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def unambiguous_observed_activation(self):
        value = self.summary
        count = value.retained_operations
        requested = value.requested_cache_operations
        if (
            fingerprint(value.model_dump(mode="json")) != self.summary_sha256
            or not count
            or value.truncated
            or value.configured_operations != count
            or requested not in {0, count}
            or value.unknown_native_operations
            or value.unknown_cache_operations
            or value.native_sdk_invocations is None
            or value.cache_operations is None
            or value.cache_component_upper_usd is None
            or value.completions != {"completed": count}
        ):
            raise ValueError("POLICY_RUNTIME_REFERENCE_COMPLETE_UNIFORM_RETAINED_ACTIVITY_REQUIRED")
        if requested:
            hits, misses = value.states.get("hit", 0), value.states.get("native_miss", 0)
            valid = (
                set(value.states) <= {"hit", "native_miss"}
                and hits + misses == count
                and value.native_sdk_invocations == misses
                and count <= value.cache_operations <= count + misses
            )
        else:
            valid = (
                value.states == {"inactive": count}
                and value.native_sdk_invocations == count
                and value.cache_operations == 0
                and value.cache_component_upper_usd == Decimal(0)
            )
        if not valid:
            raise ValueError("POLICY_RUNTIME_REFERENCE_INCONSISTENT_ACTIVATION_COUNTS")
        return self

    @property
    def cache_requested(self):
        return self.summary.requested_cache_operations == self.summary.retained_operations

    @property
    def expires_at(self):
        # This is a freshness bound on retained client-reported metadata, not an execution lease.
        # Bound age from the earliest possible sampled operation. A fresh query
        # end must not make month-old observations appear newly measured.
        return self.summary.scope.window_start + timedelta(hours=1)

    def require_current(self, source: PolicySourceBinding, *, now=None):
        now = now or datetime.now(UTC)
        scope = self.summary.scope
        if (
            now.tzinfo is None
            or not scope.window_end <= now < self.expires_at
            or source.async_consumer
            or fingerprint(source.model_dump(mode="json")) != self.source_binding_sha256
            or scope.source_commit != source.commit
            or scope.policy_sha256 != source.policy_sha256
            or scope.workflow != source.workflow
        ):
            raise ValueError("POLICY_RUNTIME_REFERENCE_CURRENT_SOURCE_AND_FRESH_WINDOW_REQUIRED")
        return self

    def require_retained(self, summary: PolicyRuntimeSummary):
        if fingerprint(summary.model_dump(mode="json")) != self.summary_sha256:
            raise ValueError("POLICY_RUNTIME_REFERENCE_RAW_ACTIVITY_CHANGED_OR_EXPIRED")
        return self

    def public_summary(self):
        scope = self.summary.scope
        return PublicPolicyRuntimeReference(
            environment=scope.environment,
            service_name=scope.service_name,
            workflow=scope.workflow,
            call_site_id=scope.call_site_id,
            source_commit=scope.source_commit,
            window_start=scope.window_start,
            window_end=scope.window_end,
            expires_at=self.expires_at,
            cache_requested=self.cache_requested,
            retained_operations=self.summary.retained_operations,
        ).model_dump(mode="json")


class PublicPolicyRuntimeReference(StrictModel):
    version: Literal["cutback.source-policy-runtime-reference-public.v1"] = (
        "cutback.source-policy-runtime-reference-public.v1"
    )
    environment: Identifier
    service_name: Identifier
    workflow: Identifier
    call_site_id: Identifier
    source_commit: str = Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")
    window_start: AwareDatetime
    window_end: AwareDatetime
    expires_at: AwareDatetime
    cache_requested: StrictBool
    retained_operations: StrictInt = Field(ge=1, le=10000)
    capture_coverage: Literal["unknown"] = "unknown"
    identity_basis: Literal["client_reported_source_map"] = "client_reported_source_map"
    execution_authorized: Literal[False] = False


def freeze_reference(source: PolicySourceBinding, summary: PolicyRuntimeSummary, *, now=None):
    try:
        reference = PolicyRuntimeReference(
            source_binding_sha256=fingerprint(source.model_dump(mode="json")),
            summary=summary,
            summary_sha256=fingerprint(summary.model_dump(mode="json")),
        )
    except ValueError:
        raise ValueError(
            "POLICY_RUNTIME_REFERENCE_COMPLETE_UNIFORM_RETAINED_ACTIVITY_REQUIRED"
        ) from None
    return reference.require_current(source, now=now)
