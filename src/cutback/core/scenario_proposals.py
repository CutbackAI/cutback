"""Bounded scenario proposals cannot supply tools, answers, assertions or permissions."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from cutback.core.models import StrictModel

ScenarioKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class ScenarioCoverageGoal(StrictModel):
    key: ScenarioKey
    description: ShortText

    @model_validator(mode="after")
    def bytes_bounded(self):
        if len(self.description.encode()) > 500:
            raise ValueError("SCENARIO_GOAL_BYTE_BOUND")
        return self


class ScenarioRequirements(StrictModel):
    kind: Literal["task_scenarios_v1"] = "task_scenarios_v1"
    task_brief: str = Field(min_length=1, max_length=4000)
    coverage_goals: list[ScenarioCoverageGoal] = Field(min_length=1, max_length=8)
    max_scenarios: int = Field(default=4, strict=True, ge=1, le=8)
    max_personas: int = Field(default=3, strict=True, ge=1, le=4)
    max_additional_user_turns: int = Field(default=2, strict=True, ge=0, le=3)

    @model_validator(mode="after")
    def bounded(self):
        keys = [goal.key for goal in self.coverage_goals]
        if len(self.task_brief.encode()) > 4000 or len(keys) != len(set(keys)):
            raise ValueError("SCENARIO_REQUIREMENTS_BYTE_OR_DUPLICATE_GOAL_BOUND")
        return self


class ProposedPersona(StrictModel):
    key: ScenarioKey
    role: ShortText
    communication_style: ShortText
    situation: ShortText

    @model_validator(mode="after")
    def bounded(self):
        if any(
            len(value.encode()) > 500
            for value in (
                self.role,
                self.communication_style,
                self.situation,
            )
        ):
            raise ValueError("SCENARIO_PERSONA_BYTE_BOUND")
        return self


class ProposedScenario(StrictModel):
    source_anchor: str = Field(pattern=r"^training-[1-8]$")
    persona_key: ScenarioKey
    coverage_goal_keys: list[ScenarioKey] = Field(min_length=1, max_length=8)
    additional_context: str = Field(default="", max_length=500)
    user_followups: list[ShortText] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def bounded(self):
        if (
            len(self.additional_context.encode()) > 500
            or any(len(text.encode()) > 500 for text in self.user_followups)
            or len(self.coverage_goal_keys) != len(set(self.coverage_goal_keys))
            or not (self.additional_context.strip() or self.user_followups)
        ):
            raise ValueError("SCENARIO_VARIANT_BYTE_SCOPE_OR_EMPTY_CHANGE_BOUND")
        return self


class ScenarioProposal(StrictModel):
    personas: list[ProposedPersona] = Field(min_length=1, max_length=4)
    scenarios: list[ProposedScenario] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def references(self):
        keys = [persona.key for persona in self.personas]
        if len(keys) != len(set(keys)) or any(
            scenario.persona_key not in keys for scenario in self.scenarios
        ):
            raise ValueError("SCENARIO_DUPLICATE_PERSONA_OR_UNKNOWN_REFERENCE")
        return self
