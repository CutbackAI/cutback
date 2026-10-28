"""Content-free, externally reported prompt consumption; coverage remains unknown."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from cutback.core.models import Identifier, StrictModel


class PromptObservation(StrictModel):
    observation_id: UUID
    version_id: UUID
    version_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: Identifier
    workflow: Identifier
    consumer: Identifier
    release: Identifier
    occurred_at: datetime
    status: Literal["success", "error", "incomplete", "unknown"] = "unknown"
    duration_ms: float | None = Field(default=None, ge=0, le=3600000, allow_inf_nan=False)
    alias_id: UUID | None = None
    alias_revision: int | None = Field(default=None, ge=1, le=2147483647)

    @model_validator(mode="after")
    def valid_context(self):
        if not self.occurred_at.tzinfo:
            raise ValueError("PROMPT_OBSERVATION_TIMESTAMP_OFFSET_REQUIRED")
        if (self.alias_id is None) != (self.alias_revision is None):
            raise ValueError("PROMPT_OBSERVATION_ALIAS_RECEIPT_REQUIRED")
        return self


class PromptObservationBatch(StrictModel):
    observations: list[PromptObservation] = Field(min_length=1, max_length=200)
