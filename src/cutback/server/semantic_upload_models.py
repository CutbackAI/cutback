"""Bounded content-upload contracts; metadata never contains case content."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import Case, DatasetImport


class SemanticDatasetEnvelope(StrictModel):
    workflow: Identifier
    environment: Identifier = "staging"
    policy_id: UUID
    source_type: Literal["synthetic_fixture", "human_labeled", "application_records", "sdk_capture"]
    independent_labels: bool = Field(default=False, strict=True)
    representative: bool = Field(default=False, strict=True)
    provenance_ref: Identifier
    split_seed: int = Field(default=17, strict=True, ge=0, le=2**31 - 1)
    split_policy: Literal["task-aware-v2"] | None = None

    def with_cases(self, cases):
        return DatasetImport.model_validate({**self.model_dump(mode="json"), "cases": cases})


class SemanticUploadPrepare(StrictModel):
    metadata: SemanticDatasetEnvelope
    expected_cases: int = Field(strict=True, ge=1, le=512)
    expected_cases_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_bytes: int = Field(default=33554432, strict=True, ge=1, le=33554432)
    max_finalize_seconds: int = Field(default=60, strict=True, ge=1, le=120)
    max_finalize_attempts: int = Field(default=1, strict=True, ge=1, le=2)
    content_upload_approved: Literal[True]

    @field_validator("content_upload_approved", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("SEMANTIC_UPLOAD_EXPLICIT_CONTENT_APPROVAL_REQUIRED")
        return value


class SemanticUploadAppend(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    offset: int = Field(strict=True, ge=0, le=511)
    cases: list[Case] = Field(min_length=1, max_length=8)


class SemanticUploadSeal(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class SemanticUploadReceipt(StrictModel):
    id: UUID
    namespace_id: UUID
    state: str
    approval_hash: str
    specification: dict
    received_cases: int
    received_bytes: int
    dataset_id: UUID | None
    attempts: int
    uncertain_attempts: int
    failure_code: str | None
    expires_at: datetime
    billable_inference_calls: Literal[0] = 0
    data_plane_cost_usd: None = None
