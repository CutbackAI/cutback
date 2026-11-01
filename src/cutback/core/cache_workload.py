"""Content-free workload assessments cannot grant inference or source-change authority."""

from datetime import datetime
from decimal import Context, Decimal, DecimalException, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_producer import PolicySourceBinding
from cutback.core.request_profiles import fingerprint

CACHE_WORKLOAD_CONTRACT = "cache-workload-assessment-v1"
POLICY_WORKLOAD_CONTRACT = "cutback.policy-workload-assessment.v1"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MoneyText = Annotated[str, Field(strict=True, min_length=1, max_length=64)]


class PolicyWorkloadBinding(StrictModel):
    """Client-reported policy attribution, distinct from a native literal edit span."""

    version: Literal["cutback.policy-workload-source.v1"] = "cutback.policy-workload-source.v1"
    source: PolicySourceBinding
    call_site_id: Identifier

    @model_validator(mode="after")
    def synchronous(self):
        if self.source.async_consumer:
            raise ValueError("POLICY_WORKLOAD_ASYNC_REQUEST_PROFILE_UNQUALIFIED")
        return self

    @property
    def workflow_id(self):
        return self.source.workflow

    @property
    def source_commit(self):
        return self.source.commit


class CacheWorkloadAssessment(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    version: Literal["cache-workload-assessment-v1"]
    project_id: UUID
    environment: Identifier
    workflow: Identifier
    analysis_id: UUID
    analysis_sha256: Digest
    source_binding_sha256: Digest
    baseline_model: Identifier
    cache_execution_sha256: Digest
    scope: Literal["retained_client_reported_source_window"]
    source_lineage: Literal["client_reported_commit_and_callsite_not_independent_attestation"]
    valid_until: Annotated[str, Field(strict=True, max_length=64)]
    metadata_as_of: Annotated[str, Field(strict=True, max_length=64)]
    decision: Literal["prepare_separate_cache_trial", "abstain"]
    reasons: list[Identifier] = Field(max_length=16)
    profiled_attempts: int | None = Field(strict=True, ge=0, le=1000)
    modeled_potential_hits: int | None = Field(strict=True, ge=0, le=1000)
    provider_modeled_window_cost_usd: MoneyText | None
    modeled_avoided_provider_cost_usd: MoneyText | None
    declared_cache_operations: int | None = Field(strict=True, ge=0, le=2000)
    declared_cache_operation_upper_cost_usd: MoneyText | None
    modeled_net_native_window_opportunity_usd: MoneyText | None
    production_hit_rate: None
    whole_task_cost_usd: None
    realized_savings_usd: None
    quality_evidence: Literal["separate_independent_trial_required"]
    cache_delivery_benchmarked: Literal[False]
    paid_calls_authorized: Literal[False]
    source_change_authorized: Literal[False]
    provider_calls: Literal[0]
    encrypted_storage_reads: Literal[0]
    assessment_sha256: Digest

    @field_validator("valid_until", "metadata_as_of")
    @classmethod
    def aware(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("CACHE_WORKLOAD_TIMESTAMP_OFFSET_REQUIRED")
        return value

    @field_validator(
        "provider_modeled_window_cost_usd",
        "modeled_avoided_provider_cost_usd",
        "declared_cache_operation_upper_cost_usd",
        "modeled_net_native_window_opportunity_usd",
    )
    @classmethod
    def finite_money(cls, value, info):
        if value is None:
            return value
        try:
            number = Decimal(value)
            exponent = number.as_tuple().exponent
            if (
                not number.is_finite()
                or not isinstance(exponent, int)
                or not -12 <= exponent <= 18
                or number.copy_abs() > Decimal("1e18")
                or (number < 0 and info.field_name != "modeled_net_native_window_opportunity_usd")
            ):
                raise ValueError
        except (ValueError, DecimalException):
            raise ValueError("CACHE_WORKLOAD_EXACT_FINITE_MONEY_REQUIRED") from None
        return value

    @field_validator(
        "cache_delivery_benchmarked",
        "paid_calls_authorized",
        "source_change_authorized",
        mode="before",
    )
    @classmethod
    def no_authority(cls, value):
        if value is not False:
            raise ValueError("CACHE_WORKLOAD_ASSESSMENT_CANNOT_GRANT_EXECUTION")
        return value

    @field_validator("provider_calls", "encrypted_storage_reads", mode="before")
    @classmethod
    def no_calls(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError("CACHE_WORKLOAD_ASSESSMENT_MUST_BE_READ_ONLY")
        return value

    @model_validator(mode="after")
    def coherent(self):
        if (
            fingerprint(self.model_dump(mode="json", exclude={"assessment_sha256"}))
            != self.assessment_sha256
        ):
            raise ValueError("CACHE_WORKLOAD_ASSESSMENT_INTEGRITY_REQUIRED")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("CACHE_WORKLOAD_DUPLICATE_REASONS")
        if self.decision == "abstain":
            if not self.reasons:
                raise ValueError("CACHE_WORKLOAD_ABSTENTION_REQUIRES_REASONS")
            return self
        n, hits = self.profiled_attempts, self.modeled_potential_hits
        amounts = (
            self.provider_modeled_window_cost_usd,
            self.modeled_avoided_provider_cost_usd,
            self.declared_cache_operation_upper_cost_usd,
            self.modeled_net_native_window_opportunity_usd,
        )
        if (
            self.reasons
            or n is None
            or hits is None
            or not 0 < hits < n
            or any(amount is None for amount in amounts)
        ):
            raise ValueError("CACHE_WORKLOAD_TRIAL_REQUIRES_COMPLETE_EVIDENCE")
        with localcontext(Context(prec=64)):
            baseline, avoided, cache, net = (
                Decimal(amount) for amount in amounts if amount is not None
            )
            if (
                not 0 < net == avoided - cache <= avoided <= baseline
                or self.declared_cache_operations != 2 * n - hits
            ):
                raise ValueError("CACHE_WORKLOAD_TRIAL_ECONOMICS_CONFLICT")
        return self
