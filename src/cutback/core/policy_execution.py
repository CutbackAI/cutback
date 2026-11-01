"""Metadata bindings for encrypted common-policy evaluation artifacts.

Keep this module independent of experiment/model/SDK imports. The server must
load and validate current scoped encrypted payloads before approval or dispatch;
these references alone grant no content, provider or execution authority.
"""

from decimal import Context, Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_serializer,
    model_validator,
)

from cutback.core.models import Identifier, StrictModel
from cutback.core.policy_producer import PolicySourceBinding


class PolicyExecutionRef(StrictModel):
    key: Identifier
    payload_id: UUID
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    cache_enabled: StrictBool = False
    cache_operation_upper_usd: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=12)
    cache_requested: StrictBool | None = None

    @model_serializer(mode="wrap")
    def preserve_v1_wire(self, handler):
        value = handler(self)
        if self.cache_requested is None:
            value.pop("cache_requested", None)
        return value

    @field_validator("cache_operation_upper_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_EXECUTION_COST_REQUIRES_EXACT_DECIMAL")
        return value

    @model_validator(mode="after")
    def scoped_reference(self):
        if (
            not self.payload_id.int
            or not self.cache_enabled
            and self.cache_operation_upper_usd is not None
            or self.cache_requested is not None
            and self.cache_requested != self.cache_enabled
        ):
            raise ValueError("POLICY_EXECUTION_PAYLOAD_OR_CACHE_METADATA_INVALID")
        return self

    @property
    def maximum_cache_operations_per_case(self):
        return 2 if self.cache_enabled else 0

    @property
    def cache_cost_upper_per_case_usd(self):
        if not self.cache_enabled:
            return Decimal(0)
        with localcontext(Context(prec=64)):
            return (
                2 * self.cache_operation_upper_usd
                if self.cache_operation_upper_usd is not None
                else None
            )


class SourcePolicyExecutionSpec(StrictModel):
    version: Literal["cutback.source-policy-execution.v1", "cutback.source-policy-execution.v2"] = (
        "cutback.source-policy-execution.v1"
    )
    phase: Literal["evaluation", "training"] = "evaluation"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    source: PolicySourceBinding
    reference: PolicyExecutionRef
    candidates: list[PolicyExecutionRef] = Field(min_length=1, max_length=5)
    max_cases: Annotated[StrictInt, Field(ge=1, le=1024)] = 1024

    @model_validator(mode="after")
    def unique_current_references(self):
        values = [self.reference, *self.candidates]
        if (
            self.version == "cutback.source-policy-execution.v1"
            and any(value.cache_requested is not None for value in values)
            or self.version == "cutback.source-policy-execution.v2"
            and (
                self.reference.cache_requested is None
                or self.source.async_consumer
                or any(value.cache_enabled and value.cache_requested is None for value in values)
            )
            or self.reference.policy_sha256 != self.source.policy_sha256
            or len({value.key for value in values}) != len(values)
            or len({value.payload_id for value in values}) != len(values)
            or len({value.policy_sha256 for value in values}) != len(values)
            or self.phase == "training"
            and any(value.cache_enabled for value in values)
            or self.source.async_consumer
            and any(value.cache_enabled for value in values)
        ):
            raise ValueError("POLICY_EXECUTION_DISTINCT_SOURCE_BOUND_REFERENCES_REQUIRED")
        return self

    def validate_plan(self, plan):
        values = [plan.baseline, *plan.candidates]
        if (
            plan.evaluator_version != "deterministic-v19"
            or plan.workflow != self.source.workflow
            or plan.baseline.key != self.reference.key
            or [value.key for value in plan.candidates] != [value.key for value in self.candidates]
            or plan.approved_providers != ["openai"]
            or plan.episode_execution
            or plan.retrieval_execution
            or plan.compression_execution
            or plan.routing_execution
            or plan.source_binding
            or plan.prompt_bindings
            or plan.criteria_binding
            or plan.native_fixture
            or plan.workload_portfolio
            or plan.judge
            or plan.judge_rubric is not None
            or plan.pilot_samples
            or plan.staged
            or plan.stage != "full"
            or plan.repeats != 1
            or plan.sample_limit is not None
            or not plan.retain_case_outputs
            or plan.content_nonce is None
            or plan.metric.kind != "exact_label"
            or any(value.exact_cache is not None for value in values)
            or self.phase == "training"
            and (
                plan.split != "train"
                or plan.predecessor_run_id
                or any(value.routes for value in plan.candidates)
            )
            or self.phase == "evaluation"
            and plan.split not in {"validation", "test"}
            or plan.split == "test"
            and not plan.predecessor_run_id
        ):
            raise ValueError("POLICY_EXECUTION_DISTINCT_FROZEN_SOURCE_PLAN_REQUIRED")

    def public_summary(self):
        values = [self.reference, *self.candidates]
        result = {
            "version": self.version,
            "phase": self.phase,
            "transport": self.transport,
            "source": {
                "producer_file": self.source.producer_file,
                "configuration_file": self.source.configuration_file,
                "commit": self.source.commit,
                "source_tree_hash": self.source.source_tree_hash,
                "workflow": self.source.workflow,
                "max_input_bytes": self.source.max_input_bytes,
            },
            "reference_key": self.reference.key,
            "candidate_keys": [value.key for value in self.candidates],
            "max_cases": self.max_cases,
            "cache_controls": [
                {
                    "key": value.key,
                    "maximum_operations_per_case": value.maximum_cache_operations_per_case,
                    "operation_upper_usd": str(value.cache_operation_upper_usd)
                    if value.cache_operation_upper_usd is not None
                    else None,
                }
                for value in values
                if value.cache_enabled
            ],
            "cache_cost_evidence": "declared_upper_bounds_not_invoice_verified",
            "policy_contents_in_metadata": False,
            "execution_authorized": False,
        }
        if self.version == "cutback.source-policy-execution.v2":
            result["cache_activation"] = {
                value.key: value.cache_requested
                if value.cache_requested is not None
                else value.cache_enabled
                for value in values
            }
            result["uncached_training_shadow"] = self.phase == "training"
        return result


def qualified_cache(reference, policy, execution=None):
    """Resolve explicit v2 activation without mutating the encrypted source policy.

    A v2 training plan can request an uncached shadow of a cached source. Its
    metadata binds that choice; the original policy and its SHA stay intact.
    """
    cache = policy.cache_execution
    if reference.cache_requested is not None:
        if getattr(execution, "version", None) != "cutback.source-policy-execution.v2":
            raise ValueError("POLICY_EXECUTION_ACTIVATION_REQUIRES_DISTINCT_V2_BINDING")
        if reference.cache_requested is False:
            cache = None
        elif cache is None:
            raise ValueError("POLICY_EXECUTION_ACTIVATION_REQUIRES_DECLARED_CACHE")
    if reference.cache_enabled != (cache is not None) or reference.cache_operation_upper_usd != (
        cache.operation_cost_upper_usd if cache else None
    ):
        raise ValueError("POLICY_EXECUTION_EFFECTIVE_CACHE_BINDING_CHANGED")
    return cache


def effective_policy_behavior(reference, policy, execution):
    return policy.model_copy(
        update={"cache_execution": qualified_cache(reference, policy, execution)}
    ).behavior_sha256


def uncached_shadow_reference(reference: PolicyExecutionRef):
    return PolicyExecutionRef.model_validate(
        {
            **reference.model_dump(mode="json"),
            "cache_requested": False,
            "cache_enabled": False,
            "cache_operation_upper_usd": None,
        }
    )


class PublicPolicySource(StrictModel):
    producer_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    configuration_file: str = Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_./-]+$")
    commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    source_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workflow: Identifier
    max_input_bytes: Annotated[StrictInt, Field(ge=1, le=95904)]


class PublicPolicyCacheControl(StrictModel):
    key: Identifier
    maximum_operations_per_case: Literal[2]
    operation_upper_usd: Decimal | None = Field(ge=0, le=100, decimal_places=12)


class PublicPolicyExecution(StrictModel):
    """Idempotent metadata projection for plans, forecasts and result receipts."""

    version: Literal["cutback.source-policy-execution.v1", "cutback.source-policy-execution.v2"]
    phase: Literal["evaluation", "training"]
    transport: Literal["native_provider", "scripted_test_only"]
    source: PublicPolicySource
    reference_key: Identifier
    candidate_keys: list[Identifier] = Field(min_length=1, max_length=5)
    max_cases: Annotated[StrictInt, Field(ge=1, le=1024)]
    cache_controls: list[PublicPolicyCacheControl] = Field(max_length=6)
    cache_cost_evidence: Literal["declared_upper_bounds_not_invoice_verified"]
    policy_contents_in_metadata: Literal[False]
    execution_authorized: Literal[False]
    logical_requests_upper: Annotated[StrictInt, Field(ge=0, le=6144)] | None = None
    native_sdk_calls_upper: Annotated[StrictInt, Field(ge=0, le=6144)] | None = None
    cache_operations_upper: Annotated[StrictInt, Field(ge=0, le=12288)] | None = None
    heldout_quality_claim: StrictBool | None = None
    routing_compute_cost_usd: None = None
    application_hosting_cost_usd: None = None
    cache_activation: dict[Identifier, StrictBool] | None = Field(default=None, max_length=6)
    uncached_training_shadow: StrictBool | None = None
