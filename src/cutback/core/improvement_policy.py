"""Bound one plain-text application frame across native, prompt and routing choices.

This is a source/runtime contract, not an experiment approval, cost ledger or
selection result. Cache retention requires its separate private runtime scope.
No SDK, network, application import or execution occurs during validation.
"""

import json
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StrictInt, model_validator

from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.models import Identifier, StrictModel
from cutback.core.request_profiles import fingerprint
from cutback.evals.experiments import (
    ModelSpec,
    provider_models,
    qualified_native_model,
    route_target,
)

SOURCE_POLICY_BYTES_BOUND = 262144


class SourceImprovementPolicy(StrictModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    version: Literal["cutback.source-improvement-policy.v1"] = (
        "cutback.source-improvement-policy.v1"
    )
    workflow: Identifier
    max_input_bytes: Annotated[StrictInt, Field(ge=1, le=95904)]
    max_instruction_bytes: Annotated[StrictInt, Field(ge=1, le=16000)] = 16000
    baseline: ModelSpec
    strategy: ModelSpec
    cache_execution: ExactCacheExecution | None = None
    runtime_cache_activation: Literal["explicit_private_scope_file_only"] = (
        "explicit_private_scope_file_only"
    )

    @model_validator(mode="after")
    def qualified(self):
        baseline = self.baseline
        if baseline.provider != "openai" or baseline.routes:
            raise ValueError("SOURCE_POLICY_PLAIN_NATIVE_BASELINE_REQUIRED")
        targets = provider_models(self.strategy)
        if self.strategy.provider == "router":
            expected = ModelSpec(
                key=self.strategy.key,
                provider="router",
                model=self.strategy.model,
                routes=self.strategy.routes,
            )
            if (
                expected.model_dump(mode="json") != self.strategy.model_dump(mode="json")
                or self.strategy.routes[-1].max_input_bytes != self.max_input_bytes
            ):
                raise ValueError("SOURCE_POLICY_ROUTER_MUST_PRESERVE_FULL_INPUT_DOMAIN")
        fixed = baseline.model_dump(
            mode="json", exclude={"key", "model", "max_output_tokens", "instruction"}
        )
        for target in [baseline, *targets]:
            if (
                target.provider != "openai"
                or target.routes
                or target.request_contract is not None
                or target.native_contract is not None
                or target.exact_cache is not None
                or target.prompt_cache is not None
                or target.agent_turn is not None
                or len(target.instruction.encode("utf-8")) > self.max_instruction_bytes
                or target.max_output_tokens > baseline.max_output_tokens
                or target.model_dump(
                    mode="json", exclude={"key", "model", "max_output_tokens", "instruction"}
                )
                != fixed
                or self.max_input_bytes + len(target.instruction.encode()) + 4096
                > target.max_input_tokens
            ):
                raise ValueError("SOURCE_POLICY_NATIVE_FRAME_OR_BOUNDS_CHANGED")
            qualified_native_model(target)
        if len(self.configuration_bytes()) > SOURCE_POLICY_BYTES_BOUND:
            raise ValueError("SOURCE_POLICY_SERIALIZED_CONFIGURATION_BOUND_EXCEEDED")
        return self

    def configuration_bytes(self):
        return (
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=True) + "\n"
        ).encode()

    @property
    def policy_sha256(self):
        return fingerprint(self.model_dump(mode="json"))

    @property
    def behavior_sha256(self):
        """Private trial identity; aliases and redundant route splits are not variants."""
        baseline = self.baseline.model_dump(mode="json", exclude={"key"})
        segments: list[dict] = []
        routes = (
            [(value.max_input_bytes, value.target) for value in self.strategy.routes]
            if self.strategy.provider == "router"
            else [(self.max_input_bytes, self.strategy)]
        )
        for bound, target in routes:
            value = target.model_dump(mode="json", exclude={"key"})
            bound = min(bound, self.max_input_bytes)
            if segments and segments[-1]["target"] == value:
                segments[-1]["max_input_bytes"] = bound
            else:
                segments.append({"max_input_bytes": bound, "target": value})
            if bound == self.max_input_bytes:
                break
        return fingerprint(
            {
                "workflow": self.workflow,
                "max_input_bytes": self.max_input_bytes,
                "baseline": baseline,
                "segments": segments,
                "cache_execution": self.cache_execution.model_dump(mode="json")
                if self.cache_execution is not None
                else None,
                "runtime_cache_activation": self.runtime_cache_activation,
            }
        )

    def target(self, text: str):
        # Bound characters before allocating the UTF-8 representation.
        if type(text) is not str or len(text) > self.max_input_bytes:
            raise ValueError("SOURCE_POLICY_BOUNDED_TEXT_REQUIRED")
        try:
            if len(text.encode("utf-8")) > self.max_input_bytes:
                raise ValueError("SOURCE_POLICY_BOUNDED_TEXT_REQUIRED")
        except UnicodeError:
            raise ValueError("SOURCE_POLICY_VALID_UTF8_REQUIRED") from None
        return (
            route_target(self.strategy, text)
            if self.strategy.provider == "router"
            else self.strategy
        )

    def request(self, text: str):
        target = self.target(text)
        return {
            "model": target.model,
            "input": text,
            "instructions": target.instruction,
            "max_output_tokens": target.max_output_tokens,
            "store": False,
        }
