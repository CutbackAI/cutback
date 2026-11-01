"""Pure exact decoding after a caller establishes current encrypted-content authority."""

from typing import Literal
from uuid import UUID, uuid4

from pydantic import ConfigDict, model_validator

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.models import StrictModel
from cutback.core.policy_execution import (
    PolicyExecutionRef,
    SourcePolicyExecutionSpec,
    effective_policy_behavior,
    qualified_cache,
)
from cutback.core.request_profiles import fingerprint


class PolicyPayload(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.source-policy-payload.v1"] = "cutback.source-policy-payload.v1"
    nonce: UUID
    policy: SourceImprovementPolicy

    @model_validator(mode="after")
    def nonce_required(self):
        if not self.nonce.int:
            raise ValueError("POLICY_ARTIFACT_NONZERO_NONCE_REQUIRED")
        return self


def prepare_policy_payload(key, payload_id: UUID, policy: SourceImprovementPolicy):
    """Private data and metadata only; caller owns scoped encrypted persistence."""
    value = PolicyPayload(nonce=uuid4(), policy=policy)
    raw = value.model_dump(mode="json")
    cached = policy.cache_execution
    return raw, PolicyExecutionRef(
        key=key,
        payload_id=payload_id,
        payload_sha256=fingerprint(raw),
        policy_sha256=policy.policy_sha256,
        cache_enabled=cached is not None,
        cache_operation_upper_usd=cached.operation_cost_upper_usd if cached else None,
    )


def decode_policy_payload(raw, reference: PolicyExecutionRef, execution: SourcePolicyExecutionSpec):
    try:
        value = PolicyPayload.model_validate(raw)
        policy = value.policy
        cached = qualified_cache(reference, policy, execution)
        if (
            fingerprint(raw) != reference.payload_sha256
            or fingerprint(value.model_dump(mode="json")) != reference.payload_sha256
            or policy.policy_sha256 != reference.policy_sha256
            or policy.workflow != execution.source.workflow
            or policy.max_input_bytes != execution.source.max_input_bytes
            or reference.cache_enabled != (cached is not None)
            or reference.cache_operation_upper_usd
            != (cached.operation_cost_upper_usd if cached else None)
            or reference not in [execution.reference, *execution.candidates]
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise ValueError("POLICY_ARTIFACT_BINDING_OR_FRAME_CHANGED") from None
    return policy


def decode_policy_bundle(payloads: dict, execution: SourcePolicyExecutionSpec):
    """Validate all private policies as one source family before approval/dispatch.

    The caller must first establish each payload's current project/environment,
    kind, content rights and integrity. Valid individual hashes do not establish
    that independently supplied candidates preserve the reference's source frame.
    """
    references = [execution.reference, *execution.candidates]
    if not isinstance(payloads, dict) or set(payloads) != {
        value.payload_id for value in references
    }:
        raise ValueError("POLICY_ARTIFACT_EXACT_BOUNDED_BUNDLE_REQUIRED")
    values = {
        value.key: decode_policy_payload(payloads[value.payload_id], value, execution)
        for value in references
    }
    original = values[execution.reference.key]
    if len(
        {
            effective_policy_behavior(reference, values[reference.key], execution)
            for reference in references
        }
    ) != len(values):
        raise ValueError("POLICY_ARTIFACT_DUPLICATED_RUNTIME_CONFIGURATION")
    for candidate in execution.candidates:
        policy = values[candidate.key]
        if (
            policy.baseline != original.baseline
            or policy.max_instruction_bytes != original.max_instruction_bytes
            or policy.runtime_cache_activation != original.runtime_cache_activation
        ):
            raise ValueError("POLICY_ARTIFACT_CANDIDATE_CHANGED_REFERENCE_FRAME")
    return values
