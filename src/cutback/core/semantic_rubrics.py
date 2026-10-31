"""Owner-declared episode proxy dimensions; generated text cannot set thresholds."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel


class SemanticJudgeChoice(StrictModel):
    provider: Literal["openai"] = "openai"
    model: Identifier
    max_input_tokens: Annotated[StrictInt, Field(ge=256, le=262144)]
    max_output_tokens: Annotated[StrictInt, Field(ge=128, le=8192)]


class SemanticDimension(StrictModel):
    kind: Literal["answer_correctness", "retrieval_groundedness", "conversation_constraints"]
    requirement: str = Field(min_length=1, max_length=2000)
    minimum: Annotated[float, Field(strict=True, ge=0, le=1)]


class SemanticRubricRequirements(StrictModel):
    kind: Literal["episode_semantic_rubrics_v1"] = "episode_semantic_rubrics_v1"
    task_brief: str = Field(min_length=1, max_length=4000)
    dimensions: list[SemanticDimension] = Field(min_length=1, max_length=3)
    judge: SemanticJudgeChoice
    minimum: float = Field(ge=0, le=1)
    max_regression: float = Field(default=0, ge=0, le=1)
    min_test_samples: Annotated[StrictInt, Field(ge=1, le=10000)] = 30
    min_test_groups: Annotated[StrictInt, Field(ge=1, le=10000)] = 3

    @model_validator(mode="after")
    def bounded(self):
        if len(self.task_brief.encode()) > 4000 or any(
            len(dimension.requirement.encode()) > 2000 for dimension in self.dimensions
        ):
            raise ValueError("SEMANTIC_RUBRIC_OWNER_REQUIREMENTS_BYTE_BOUND")
        if len(
            {(dimension.kind, dimension.requirement.strip()) for dimension in self.dimensions}
        ) != len(self.dimensions):
            raise ValueError("SEMANTIC_RUBRIC_OWNER_DIMENSIONS_MUST_BE_UNIQUE")
        return self
