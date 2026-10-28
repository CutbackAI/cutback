"""Explicit prompt authoring, purpose-bound reads and compare-and-swap mutations."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, StrictBool, StringConstraints, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.core.prompts import PromptDocument, VariableName
from cutback.evals.experiments import ExperimentPlan

PromptKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$")]
AliasName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")]
EnvironmentName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]


class PromptCreate(StrictModel):
    key: PromptKey
    workflow: Identifier


class PromptVersionInput(StrictModel):
    version_id: UUID
    expected_latest_ordinal: int = Field(ge=0, le=2147483646)
    policy_id: UUID
    environment: EnvironmentName
    provenance_ref: Identifier
    document: PromptDocument


class PromptContext(StrictModel):
    purpose: Identifier
    environment: EnvironmentName
    workflow: Identifier


class PromptResolve(PromptContext):
    provider: Literal["local", "openai", "anthropic"]
    version_id: UUID | None = None
    alias: AliasName | None = None

    @model_validator(mode="after")
    def pinned_or_alias(self):
        if (self.version_id is None) == (self.alias is None):
            raise ValueError("SELECT_ONE_PINNED_PROMPT_OR_ALIAS")
        return self


class PromptRender(PromptContext):
    variables: dict[VariableName, JsonValue] = Field(max_length=20)
    values_authorized: StrictBool


class PromptDiff(PromptContext):
    left_version_id: UUID
    right_version_id: UUID


class PromptPromote(StrictModel):
    version_id: UUID
    environment: EnvironmentName
    alias: AliasName
    expected_revision: int = Field(ge=0, le=2147483646)
    expected_version_id: UUID | None = None
    approval_ref: Identifier
    quality_unverified_acknowledged: StrictBool
    evidence_run_id: UUID | None = None
    evidence_model_key: Identifier | None = None

    @model_validator(mode="after")
    def expected_head(self):
        if (self.expected_revision == 0) != (self.expected_version_id is None):
            raise ValueError("PROMPT_ALIAS_EXPECTED_HEAD_REQUIRED")
        if (self.evidence_run_id is None) != (self.evidence_model_key is None):
            raise ValueError("PROMPT_ALIAS_EVIDENCE_BINDING_INCOMPLETE")
        if self.evidence_run_id is None and not self.quality_unverified_acknowledged:
            raise ValueError("MANUAL_PROMPT_APPROVAL_REQUIRES_UNVERIFIED_QUALITY_ACKNOWLEDGMENT")
        return self


class PromptRollback(StrictModel):
    receipt_id: UUID
    expected_revision: int = Field(ge=1, le=2147483646)
    approval_ref: Identifier


class PromptGrantInput(StrictModel):
    actor: str = Field(pattern=r"^(?:[a-f0-9-]{36}|oidc:[a-f0-9]{32})$")
    environment: EnvironmentName
    workflow: Identifier
    policy_id: UUID
    expires_at: datetime
    approval_ref: Identifier
    expected_revision: int = Field(default=0, ge=0, le=2147483646)

    @model_validator(mode="after")
    def aware(self):
        if not self.expires_at.tzinfo:
            raise ValueError("PROMPT_GRANT_EXPIRY_REQUIRES_OFFSET")
        return self


class PromptSelection(StrictModel):
    model_key: Identifier
    version_id: UUID
    environment: EnvironmentName
    prompt_variables: dict[VariableName, JsonValue] = Field(default_factory=dict, max_length=20)
    sampling_options: dict[str, JsonValue] = Field(default_factory=dict, max_length=4)


class PromptPlayground(StrictModel):
    dataset_id: UUID
    specification: ExperimentPlan
    selections: list[PromptSelection] = Field(min_length=1, max_length=6)
    purpose: Identifier
