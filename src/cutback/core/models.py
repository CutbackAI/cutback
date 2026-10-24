from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.:/-]{1,200}$")]
TokenCount = Annotated[int, Field(strict=True, ge=0, le=10**12)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class LLMEvent(StrictModel):
    event_schema_version: Literal["1.0"] = "1.0"
    event_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    environment: Identifier
    service_name: Identifier
    sdk_version: Identifier = "0.1.0.dev0"
    trace_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    span_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")]
    parent_span_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{16}$")] | None = None
    request_attempt_id: UUID = Field(default_factory=uuid4)
    logical_request_id: UUID | None = None
    call_site_id: Identifier = "unattributed"
    workflow_id: Identifier | None = None
    source_commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")] | None = None
    provider: Literal["openai", "anthropic"]
    sdk_surface: Literal["responses.create", "chat.completions.create", "messages.create"]
    requested_model: Identifier | None = None
    response_model: Identifier | None = None
    region: Identifier | None = None
    deployment: Identifier | None = None
    service_tier: Identifier | None = None
    duration_ms: Annotated[float, Field(ge=0, le=86400000)]
    status: Literal["success", "error", "cancelled", "incomplete"]
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    error_type: Identifier | None = None
    retry_number: Annotated[int, Field(ge=0, le=100)] | None = None
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    cache_read_tokens: TokenCount | None = None
    cache_write_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None
    usage_source: Literal["provider_reported", "counted", "estimated", "unavailable"] = (
        "unavailable"
    )
    metering_status: Literal["text_only", "unsupported_units", "unknown"] = "unknown"
    content_capture_status: Literal["disabled"] = "disabled"
    sampling_policy_id: Literal["all-supported-calls-v1"] = "all-supported-calls-v1"
    # No arbitrary attributes, raw usage maps, request IDs, error messages, or content fields.

    @field_validator("occurred_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("UTC offset required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def usage_consistency(self):
        values = [
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.reasoning_tokens,
        ]
        if self.usage_source == "unavailable" and any(v is not None for v in values):
            raise ValueError("unavailable usage must remain null")
        if self.provider == "openai":
            if (
                self.input_tokens is not None
                and self.cache_read_tokens is not None
                and self.cache_read_tokens > self.input_tokens
            ):
                raise ValueError("cached input is a subset of OpenAI input")
        if (
            self.output_tokens is not None
            and self.reasoning_tokens is not None
            and self.reasoning_tokens > self.output_tokens
        ):
            raise ValueError("reasoning is a subset of output")
        return self


class EventBatch(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    project_id: UUID
    events: Annotated[list[LLMEvent], Field(min_length=1, max_length=200)]


class IngestReceipt(StrictModel):
    accepted: int
    duplicate: int
    rejected: int = 0
    request_id: UUID
