"""Explicit content-use and cost contracts for online quality evaluation."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.evals.experiments import ModelSpec
from cutback.pricing import Catalog
from cutback.server.monitoring_contracts import SpanFilter
from cutback.server.prompt_contracts import EnvironmentName

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "outcome": {"type": "string", "enum": ["passed", "failed", "unknown"]},
        "evidence_quote": {"type": "string"},
    },
    "required": ["outcome", "evidence_quote"],
    "additionalProperties": False,
}


class QualityTarget(StrictModel):
    provider: Literal["openai", "anthropic", "local"]
    model: Identifier
    surface: Literal["responses.create", "chat.completions.create", "messages.create"]
    max_input_tokens: int = Field(default=8192, ge=1, le=100000)
    max_output_tokens: int = Field(default=256, ge=16, le=16384)
    local_cost_upper_usd: Decimal | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def qualified(self):
        from cutback.evals.experiments import qualified_native_model

        model = self.model_spec()
        qualified_native_model(model)
        if (
            (self.provider == "anthropic") != (self.surface == "messages.create")
            or self.provider == "local"
            and self.surface != "chat.completions.create"
        ):
            raise ValueError("QUALITY_NATIVE_SURFACE_UNSUPPORTED")
        return self

    def model_spec(self):
        return ModelSpec(
            key="online-quality-judge",
            provider=self.provider,
            model=self.model,
            max_input_tokens=self.max_input_tokens,
            max_output_tokens=self.max_output_tokens,
            local_cost_upper_usd=self.local_cost_upper_usd,
        )


class QualityRuleInput(StrictModel):
    version_id: UUID
    key: Identifier
    expected_revision: int = Field(ge=0, le=2147483646)
    environment: EnvironmentName
    workflow: Identifier
    filter: SpanFilter = Field(default_factory=SpanFilter)
    source_policy_id: UUID
    evaluator: Literal["nonempty_output", "output_schema", "model_judge"]
    prompt_version_id: UUID | None = None
    judge: QualityTarget | None = None
    content_use_authorized: StrictBool = False
    record_input_features: StrictBool = False
    retain_judge_output: StrictBool = False
    sample_basis_points: int = Field(default=10000, ge=1, le=10000)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    max_evaluations: int = Field(ge=1, le=50000)
    max_requests: int = Field(default=0, ge=0, le=10000)
    max_total_usd: Decimal = Field(default=Decimal(0), ge=0, le=10000)
    wait_seconds: int = Field(default=300, ge=1, le=86400)
    approval_ref: Identifier
    price_sha256: str = Field(default_factory=lambda: Catalog().sha256, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def authority(self):
        if self.price_sha256 != Catalog().sha256:
            raise ValueError("PRICE_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
        if not self.content_use_authorized:
            raise ValueError("EXPLICIT_ONLINE_CONTENT_USE_APPROVAL_REQUIRED")
        if not self.starts_at < self.expires_at or self.expires_at - self.starts_at > timedelta(
            days=30
        ):
            raise ValueError("QUALITY_RULE_WINDOW_INVALID")
        if self.filter.workflow not in {None, self.workflow}:
            raise ValueError("QUALITY_FILTER_WORKFLOW_MISMATCH")
        if self.evaluator == "model_judge":
            if self.judge is None or self.prompt_version_id is None or self.max_requests == 0:
                raise ValueError("QUALITY_JUDGE_PROMPT_MODEL_AND_REQUEST_CAP_REQUIRED")
            from cutback.evals.experiments import upper_cost

            if upper_cost(self.judge.model_spec()) > self.max_total_usd:
                raise ValueError("QUALITY_BUDGET_BELOW_ONE_WORST_CASE_REQUEST")
        elif (
            self.judge is not None
            or self.max_requests
            or self.max_total_usd
            or self.retain_judge_output
        ):
            raise ValueError("DETERMINISTIC_QUALITY_PROFILE_CANNOT_AUTHORIZE_PROVIDER_CALLS")
        if (self.evaluator != "nonempty_output") != (self.prompt_version_id is not None):
            raise ValueError("QUALITY_SCHEMA_OR_JUDGE_REQUIRES_PINNED_PROMPT")
        return self
