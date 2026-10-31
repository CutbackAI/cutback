"""Additive authority for train-only fitting and frozen routing evaluation."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from cutback.core.models import StrictModel


class RoutingTrainingExecution(StrictModel):
    phase: Literal["training"] = "training"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    max_cases: Annotated[StrictInt, Field(ge=2, le=256)] = 256


class LearnedRoutingBinding(StrictModel):
    phase: Literal["fitted_policy"] = "fitted_policy"
    transport: Literal["native_provider", "scripted_test_only"] = "native_provider"
    fit_id: UUID
    fit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_run_id: UUID

    @model_validator(mode="after")
    def identities(self):
        if not self.fit_id.int or not self.training_run_id.int:
            raise ValueError("LEARNED_ROUTING_IDENTITIES_REQUIRED")
        return self
