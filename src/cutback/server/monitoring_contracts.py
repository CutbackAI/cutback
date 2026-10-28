"""Bounded metadata-only continuous evaluation and incident contracts."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, model_serializer, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.server.prompt_contracts import EnvironmentName


class SpanFilter(StrictModel):
    workflow: Identifier | None = None
    service: Identifier | None = None
    release: Identifier | None = None
    kind: (
        Literal[
            "agent",
            "chain",
            "llm",
            "tool",
            "retriever",
            "reranker",
            "embedding",
            "guardrail",
            "evaluator",
            "prompt",
            "unknown",
        ]
        | None
    ) = None


class OnlineEvaluator(StrictModel):
    kind: Literal["execution_ok", "duration_le", "external_boolean"]
    max_duration_ms: Decimal | None = Field(default=None, ge=0, le=86400000)
    source: Identifier | None = None
    evaluator: Identifier | None = None
    evaluator_version: Identifier | None = None
    source_origin: (
        Literal["external", "cutback_model_judge", "cutback_technical_contract"] | None
    ) = None

    @model_serializer(mode="wrap")
    def preserve_legacy_specification(self, handler):
        result = handler(self)
        if self.source_origin is None:
            result.pop("source_origin", None)
        return result

    @model_validator(mode="after")
    def shape(self):
        external = (self.source, self.evaluator, self.evaluator_version)
        if self.kind == "external_boolean":
            if not all(external) or self.max_duration_ms is not None:
                raise ValueError("EXACT_EXTERNAL_EVALUATOR_REQUIRED")
        elif (
            any(external)
            or self.source_origin is not None
            or (self.kind == "duration_le") != (self.max_duration_ms is not None)
        ):
            raise ValueError("EVALUATOR_OPTIONS_MISMATCH")
        return self


class OnlineRuleInput(StrictModel):
    version_id: UUID
    key: Identifier
    expected_revision: int = Field(ge=0, le=2147483646)
    environment: EnvironmentName
    filter: SpanFilter = Field(default_factory=SpanFilter)
    evaluator: OnlineEvaluator
    sample_basis_points: int = Field(default=10000, ge=1, le=10000)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    max_evaluations: int = Field(ge=1, le=50000)
    # This execution profile has no provider calls. A nonzero ceiling must never imply
    # judge support or authorize an unimplemented billable operation.
    max_total_usd: Decimal = Field(default=Decimal(0), ge=0, le=0)
    wait_seconds: int = Field(default=300, ge=1, le=86400)
    approval_ref: Identifier

    @model_validator(mode="after")
    def window(self):
        if not self.starts_at < self.expires_at or self.expires_at - self.starts_at > timedelta(
            days=30
        ):
            raise ValueError("ONLINE_RULE_WINDOW_INVALID")
        return self


class RuleSwitch(StrictModel):
    expected_revision: int = Field(ge=1)
    expected_enabled: StrictBool
    enabled: StrictBool


class AlertRuleInput(StrictModel):
    version_id: UUID
    key: Identifier
    expected_revision: int = Field(ge=0, le=2147483646)
    online_rule_id: UUID
    window_seconds: int = Field(default=300, ge=60, le=86400)
    grace_seconds: int = Field(default=300, ge=0, le=86400)
    min_samples: int = Field(default=10, ge=1, le=50000)
    signal: Literal["failure_rate", "evaluator_error_rate", "missing_rate"] = "failure_rate"
    threshold: Decimal = Field(gt=0, le=1)
    cooldown_seconds: int = Field(default=3600, ge=0, le=2592000)


class IncidentTransition(StrictModel):
    expected_revision: int = Field(ge=1)
    action: Literal["acknowledge", "assign", "resolve"]
    owner: Identifier | None = None

    @model_validator(mode="after")
    def owner_required(self):
        if (self.action == "assign") != (self.owner is not None):
            raise ValueError("OWNER_REQUIRED_ONLY_FOR_ASSIGN")
        return self


class SavedViewInput(StrictModel):
    view_id: UUID
    key: Identifier
    expected_revision: int = Field(ge=0, le=2147483646)
    environment: EnvironmentName
    filter: SpanFilter = Field(default_factory=SpanFilter)
    page: Literal["traces", "incidents"]
    incident_state: Literal["open", "acknowledged", "resolved"] | None = None
