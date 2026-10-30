"""Explicit isolated SQL episode contracts; never fixture response declarations."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from cutback.core.episodes import EpisodeAssertions, bounded_episode_value
from cutback.core.models import Identifier, StrictModel
from cutback.core.tool_policy import RecordGrant, RecordSeed


class RecordExecutionScope(StrictModel):
    project_id: UUID
    principal_id: UUID
    broker_id: UUID
    environment: Identifier
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    max_wall_seconds: int = Field(default=60, strict=True, ge=1, le=120)
    max_result_bytes: int = Field(default=16000, strict=True, ge=256, le=64000)
    content_use_approved: Literal[True]

    @field_validator("content_use_approved", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("ISOLATED_EPISODE_EXPLICIT_CONTENT_CONSENT_REQUIRED")
        return value

    @model_validator(mode="after")
    def nonzero_scope(self):
        if self.project_id.int == 0 or self.principal_id.int == 0 or self.broker_id.int == 0:
            raise ValueError("ISOLATED_EPISODE_NONZERO_SCOPE_REQUIRED")
        return self


class IsolatedRecordExecution(RecordExecutionScope):
    version: Literal["isolated-record-tools-v1"] = "isolated-record-tools-v1"
    max_sessions: int = Field(default=32, strict=True, ge=1, le=128)
    max_total_tool_calls: int = Field(default=512, strict=True, ge=1, le=4096)


class IsolatedSecurityExecution(RecordExecutionScope):
    """Larger finite suite bounds, only accepted by the distinct v9 contract."""

    version: Literal["isolated-record-security-tools-v1"] = "isolated-record-security-tools-v1"
    max_sessions: int = Field(default=128, strict=True, ge=1, le=1000)
    max_total_tool_calls: int = Field(default=4096, strict=True, ge=1, le=100000)


class IsolatedRecordScenario(StrictModel):
    schema_version: Literal["cutback.isolated-record-episode.v1"] = (
        "cutback.isolated-record-episode.v1"
    )
    execution: Literal["isolated_record_tools"] = "isolated_record_tools"
    system: str = Field(default="", max_length=16000)
    user_followups: list[Annotated[str, Field(min_length=1, max_length=16000)]] = Field(
        default_factory=list, max_length=16
    )
    seeds: list[RecordSeed] = Field(min_length=1, max_length=64)
    grants: list[RecordGrant] = Field(min_length=1, max_length=64)
    write_approved: StrictBool = False
    assertions: EpisodeAssertions = Field(default_factory=EpisodeAssertions)
    max_turns: Annotated[StrictInt, Field(ge=1, le=32)] = 8
    max_tool_calls: Annotated[StrictInt, Field(ge=1, le=100)] = 16

    @model_validator(mode="after")
    def bounded_authority(self):
        known = {"records_read", "records_patch"}
        required = set(self.assertions.required_tools)
        required.update(check.tool for check in self.assertions.arguments)
        for path in self.assertions.allowed_paths:
            required.update(path)
        if not required <= known:
            raise ValueError("ISOLATED_EPISODE_ASSERTION_REFERENCES_UNAVAILABLE_TOOL")
        if not self.write_approved and any(grant.transitions for grant in self.grants):
            raise ValueError("ISOLATED_EPISODE_WRITE_CONSENT_REQUIRED")
        identifiers = [seed.id for seed in self.seeds]
        resources = [grant.resource_id for grant in self.grants]
        if len(set(identifiers)) != len(identifiers) or len(set(resources)) != len(resources):
            raise ValueError("ISOLATED_EPISODE_UNIQUE_RECORDS_AND_GRANTS_REQUIRED")
        if not set(resources) <= set(identifiers):
            raise ValueError("ISOLATED_EPISODE_GRANTS_REQUIRE_DECLARED_SEEDS")
        if any(
            check.user_turn > 1 + len(self.user_followups) for check in self.assertions.conversation
        ):
            raise ValueError("ISOLATED_EPISODE_CONVERSATION_CHECK_REQUIRES_USER_TURN")
        if any(
            value is not None
            for value in (
                self.assertions.min_retrieval_precision,
                self.assertions.min_retrieval_recall,
                self.assertions.min_ndcg,
                self.assertions.min_citation_support,
            )
        ):
            raise ValueError("ISOLATED_RECORD_TOOLS_DO_NOT_PROVIDE_RETRIEVAL_OR_CITATION_EVIDENCE")
        bounded_episode_value(self.model_dump(mode="json"))
        return self
