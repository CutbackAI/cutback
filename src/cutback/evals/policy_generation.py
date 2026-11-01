"""Bounded train-only policy instructions; no dynamic code, SDK calls or authority."""

import json
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.core.models import Identifier, StrictModel
from cutback.evals.campaigns import (
    GENERATOR_INSTRUCTION,
    NativeGeneratorSpec,
    NativeProposals,
    proposal_model,
)
from cutback.evals.experiments import Budget, ModelSpec
from cutback.pricing import Catalog


class PolicyGenerationFeedback(StrictModel):
    candidate_key: Identifier
    quality_gate_passed: StrictBool
    sample_metric_min: float | None = Field(default=None, ge=0, le=1)
    cost_complete: StrictBool
    known_cost_subtotal_usd: Decimal | None = Field(default=None, ge=0, le=10000, decimal_places=12)
    requests: Annotated[StrictInt, Field(ge=1, le=1024)] | None = None

    @field_validator("known_cost_subtotal_usd", mode="before")
    @classmethod
    def exact_money(cls, value):
        if value is not None and type(value) not in {str, int, Decimal}:
            raise ValueError("POLICY_GENERATION_EXACT_COST_TOTAL_REQUIRED")
        return value

    @model_validator(mode="after")
    def complete_cost(self):
        if self.cost_complete and (self.known_cost_subtotal_usd is None or self.requests is None):
            raise ValueError("POLICY_GENERATION_COMPLETE_COST_AND_REQUESTS_REQUIRED")
        return self


class PolicyGenerationPlan(StrictModel):
    operation: Literal["source-policy-generation-v1"] = "source-policy-generation-v1"
    program_id: UUID
    program_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    round_index: Annotated[StrictInt, Field(ge=1, le=3)]
    workflow: Identifier
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_payload_id: UUID
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline: ModelSpec
    budget: Budget
    price_sha256: str = Field(default_factory=lambda: Catalog().sha256, pattern=r"^[a-f0-9]{64}$")

    @property
    def candidates(self):
        return []

    @property
    def judge(self):
        return None

    @property
    def episode_execution(self):
        return None

    @model_validator(mode="after")
    def owned_single_request(self):
        if (
            not self.program_id.int
            or not self.input_payload_id.int
            or self.budget.max_requests != 1
            or self.price_sha256 != Catalog().sha256
            or self.baseline.instruction != GENERATOR_INSTRUCTION
        ):
            raise ValueError("POLICY_GENERATION_REQUIRES_ONE_OWNED_APPROVED_CALL")
        seed = self.baseline.model_copy(update={"instruction": "", "request_contract": None})
        if self.baseline != proposal_model(NativeGeneratorSpec(model=seed)):
            raise ValueError("POLICY_GENERATION_OWNED_WIRE_CONTRACT_REQUIRED")
        return self


def seeds(policies):
    return {
        key: value
        for key, value in policies.items()
        if not value.strategy.routes and value.cache_execution is None
    }


def _check_sources(specification, policies):
    execution = specification.template.source_policy_execution
    if (
        execution is None
        or not isinstance(policies, dict)
        or execution.reference.key not in policies
    ):
        raise ValueError("POLICY_GENERATION_FROZEN_SOURCE_POLICIES_REQUIRED")
    references = {value.key: value for value in [execution.reference, *execution.candidates]}
    if any(
        key not in references or value.policy_sha256 != references[key].policy_sha256
        for key, value in policies.items()
    ):
        raise ValueError("POLICY_GENERATION_FROZEN_SOURCE_POLICIES_REQUIRED")


def _document(specification, policies, training, feedback, previous):
    return {
        "version": "cutback.source-policy-generation-input.v1",
        "workflow": specification.template.workflow,
        "required_quality": specification.template.metric.model_dump(mode="json"),
        "required_criteria": [
            value.model_dump(mode="json")
            for value in specification.template.criteria
            if value.required
        ],
        "source_output_ceiling": specification.template.baseline.max_output_tokens,
        "instruction_byte_limit": specification.generator.max_instruction_bytes,
        "variant_limit": specification.generator.variants_per_round,
        "output_bounds": [
            specification.generator.min_output_tokens,
            specification.generator.max_output_tokens,
        ],
        "seeds": [
            {
                "key": key,
                "model": value.strategy.model,
                "instruction": value.strategy.instruction,
                "max_output_tokens": value.strategy.max_output_tokens,
            }
            for key, value in seeds(policies).items()
        ],
        "training": training,
        "aggregate_validation_feedback": [value.model_dump(mode="json") for value in feedback],
        "feedback_cost_basis": "exact_total_and_request_count_no_rounded_per_request_cost",
        "previous_variants": [
            {
                "model": value.strategy.model,
                "instruction": value.strategy.instruction,
                "max_output_tokens": value.strategy.max_output_tokens,
            }
            for value in previous
        ],
    }


def _check_previous(specification, policies, previous):
    generator = specification.generator
    original = policies[specification.template.source_policy_execution.reference.key]
    allowed = [
        value.strategy.model_dump(mode="json", exclude={"key", "instruction", "max_output_tokens"})
        for value in seeds(policies).values()
    ]
    for value in previous:
        if (
            not isinstance(value, SourceImprovementPolicy)
            or value.baseline != original.baseline
            or value.workflow != original.workflow
            or value.max_input_bytes != original.max_input_bytes
            or value.max_instruction_bytes != original.max_instruction_bytes
            or value.runtime_cache_activation != original.runtime_cache_activation
            or value.cache_execution is not None
            or value.strategy.routes
            or value.strategy.model_dump(
                mode="json", exclude={"key", "instruction", "max_output_tokens"}
            )
            not in allowed
            or len(value.strategy.instruction.encode()) > generator.max_instruction_bytes
            or not generator.min_output_tokens
            <= value.strategy.max_output_tokens
            <= generator.max_output_tokens
        ):
            raise ValueError("POLICY_GENERATION_PREVIOUS_VARIANT_OUTSIDE_FROZEN_FRAME")


def input_upper_bytes(specification, dataset, policies):
    generator = specification.generator
    if generator is None:
        return 0
    _check_sources(specification, policies)
    template = json.dumps(_document(specification, policies, [], [], []), ensure_ascii=False)
    training = sorted(
        (
            len(
                json.dumps(
                    {"input": item["case"]["input"], "gold": item["case"].get("gold")},
                    ensure_ascii=False,
                ).encode()
            )
            for item in dataset["items"]
            if item["split"] == "train"
        ),
        reverse=True,
    )
    prior = (generator.rounds - 1) * generator.variants_per_round
    feedback = len(policies) + (generator.rounds - 1) * (1 + generator.variants_per_round)
    model = proposal_model(generator)
    if model.request_contract is None:
        raise ValueError("POLICY_GENERATION_OWNED_WIRE_CONTRACT_REQUIRED")
    options = json.dumps(model.request_contract.options)
    return (
        len(template.encode())
        + sum(training[: generator.max_training_examples])
        + generator.max_training_examples * 4
        + prior * (6 * generator.max_instruction_bytes + 2048)
        + feedback * 1024
        + len(GENERATOR_INSTRUCTION.encode())
        + len(options.encode())
        + 4096
    )


def generation_input(specification, dataset, policies, round_index, *, feedback=(), previous=()):
    generator = specification.generator
    if (
        generator is None
        or type(round_index) is not int
        or not 1 <= round_index <= generator.rounds
    ):
        raise ValueError("POLICY_GENERATION_ROUND_OUTSIDE_FROZEN_ENVELOPE")
    _check_sources(specification, policies)
    if dataset["immutable_hash"] != specification.template.dataset_hash:
        raise ValueError("POLICY_GENERATION_FROZEN_DATASET_REQUIRED")
    if len(previous) > (round_index - 1) * generator.variants_per_round or len(feedback) > len(
        policies
    ) + (round_index - 1) * (1 + generator.variants_per_round):
        raise ValueError("POLICY_GENERATION_HISTORY_BOUND")
    _check_previous(specification, policies, previous)
    feedback = [PolicyGenerationFeedback.model_validate(value) for value in feedback]
    training, families = [], []
    for item in dataset["items"]:
        if item["split"] != "train" or item["group_hash"] in families:
            continue
        families.append(item["group_hash"])
        training.append({"input": item["case"]["input"], "gold": item["case"]["gold"]})
        if len(training) == generator.max_training_examples:
            break
    if not training:
        raise ValueError("POLICY_GENERATION_TRAIN_ONLY_EXAMPLES_REQUIRED")
    text = json.dumps(
        _document(specification, policies, training, feedback, previous), ensure_ascii=False
    )
    model = proposal_model(generator)
    if model.request_contract is None:
        raise ValueError("POLICY_GENERATION_OWNED_WIRE_CONTRACT_REQUIRED")
    options = json.dumps(model.request_contract.options)
    if (
        len((text + model.instruction + options).encode()) + 4096 > model.max_input_tokens
        or input_upper_bytes(specification, dataset, policies) > model.max_input_tokens
    ):
        raise ValueError("POLICY_GENERATION_COMPLETE_INPUT_BOUND_EXCEEDED")
    return text, families


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("POLICY_GENERATION_DUPLICATE_JSON_KEY")
        value[key] = item
    return value


def _nonfinite(value):
    raise ValueError("POLICY_GENERATION_NONFINITE_NUMBER")


def decode_policy_proposals(text, specification, policies, round_index, *, previous=()):
    _check_sources(specification, policies)
    generator = specification.generator
    if (
        generator is None
        or type(round_index) is not int
        or not 1 <= round_index <= generator.rounds
    ):
        raise ValueError("POLICY_GENERATION_ROUND_OUTSIDE_FROZEN_ENVELOPE")
    if len(previous) > (round_index - 1) * generator.variants_per_round:
        raise ValueError("POLICY_GENERATION_HISTORY_BOUND")
    _check_previous(specification, policies, previous)
    try:
        if type(text) is not str or len(text) > 100000 or len(text.encode()) > 100000:
            raise ValueError
        raw = json.loads(text, object_pairs_hook=_unique, parse_constant=_nonfinite)
        values = NativeProposals.model_validate(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("POLICY_GENERATION_BOUNDED_RESPONSE_SCHEMA_REQUIRED") from None
    if len(values.proposals) > generator.variants_per_round:
        raise ValueError("POLICY_GENERATION_VARIANT_COUNT_EXCEEDS_ENVELOPE")
    approved = seeds(policies)
    seen = {value.behavior_sha256 for value in [*policies.values(), *previous]}
    result = {}
    for index, proposed in enumerate(values.proposals):
        if (
            proposed.seed_key not in approved
            or len(proposed.instruction.encode()) > generator.max_instruction_bytes
            or not generator.min_output_tokens
            <= proposed.max_output_tokens
            <= generator.max_output_tokens
        ):
            raise ValueError("POLICY_GENERATION_PROPOSAL_OUTSIDE_FROZEN_FRAME")
        raw = approved[proposed.seed_key].model_dump(mode="json")
        raw["strategy"].update(
            instruction=proposed.instruction, max_output_tokens=proposed.max_output_tokens
        )
        try:
            policy = SourceImprovementPolicy.model_validate(raw)
        except ValueError:
            raise ValueError("POLICY_GENERATION_PROPOSAL_OUTSIDE_FROZEN_FRAME") from None
        if policy.behavior_sha256 in seen:
            raise ValueError("POLICY_GENERATION_DUPLICATED_RUNTIME_CONFIGURATION")
        seen.add(policy.behavior_sha256)
        result[f"policy-generated-r{round_index}-{index + 1}"] = policy
    return result
