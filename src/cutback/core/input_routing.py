"""Additive authority for train-only fitting and frozen routing evaluation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_serializer, model_validator

from cutback.core.models import StrictModel
from cutback.core.routing_producer import RoutingSourceBinding

SOURCE_DATASET_CASE_BOUND = 1024
SOURCE_DATASET_CONTENT_BOUND = 16 * 1024 * 1024


class RoutingTrainingExecution(StrictModel):
    phase: Literal["training"] = "training"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    max_cases: Annotated[StrictInt, Field(ge=2, le=256)] = 256
    source: RoutingSourceBinding | None = None

    @model_serializer(mode="wrap")
    def preserve_v15(self, handler):
        result = handler(self)
        if self.source is None:
            result.pop("source", None)
        return result


class LearnedRoutingBinding(StrictModel):
    phase: Literal["fitted_policy"] = "fitted_policy"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    fit_id: UUID
    fit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_run_id: UUID
    source: RoutingSourceBinding | None = None

    @model_serializer(mode="wrap")
    def preserve_v15(self, handler):
        result = handler(self)
        if self.source is None:
            result.pop("source", None)
        return result

    @model_validator(mode="after")
    def identities(self):
        if not self.fit_id.int or not self.training_run_id.int:
            raise ValueError("LEARNED_ROUTING_IDENTITIES_REQUIRED")
        return self
