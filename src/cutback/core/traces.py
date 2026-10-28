"""Versioned metadata contracts. No arbitrary attributes or captured content."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from cutback.core.models import Identifier, StrictModel

TraceID = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
SpanID = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{16}$")]
Nano = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


class WorkflowSpan(StrictModel):
    trace_id: TraceID
    span_id: SpanID
    parent_span_id: SpanID | None = None
    session_id: Identifier | None = None
    operation: Identifier
    kind: Literal[
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
    ] = "unknown"
    service: Identifier
    workflow: Identifier | None = None
    release: Identifier | None = None
    started_ns: Nano
    ended_ns: Nano | None = None
    status: Literal["ok", "error", "cancelled", "incomplete", "unknown"] = "unknown"

    @model_validator(mode="after")
    def valid_span(self):
        if not int(self.trace_id, 16) or not int(self.span_id, 16):
            raise ValueError("NONZERO_IDENTITIES_REQUIRED")
        if self.parent_span_id and not int(self.parent_span_id, 16):
            raise ValueError("NONZERO_PARENT_REQUIRED")
        if self.ended_ns is not None and self.ended_ns < self.started_ns:
            raise ValueError("END_PRECEDES_START")
        return self


class SpanBatch(StrictModel):
    profile: Literal["cutback.workflow.v1"] = "cutback.workflow.v1"
    spans: list[WorkflowSpan] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def fresh(self):
        now = datetime.now(UTC)
        earliest = int((now - timedelta(days=30)).timestamp() * 1_000_000_000)
        latest = int((now + timedelta(minutes=5)).timestamp() * 1_000_000_000)
        if any(
            s.started_ns < earliest or (s.ended_ns or s.started_ns) > latest for s in self.spans
        ):
            raise ValueError("SPAN_OUTSIDE_INGEST_WINDOW")
        return self


class EvaluationResult(StrictModel):
    result_id: UUID
    source: Identifier
    evaluator: Identifier
    evaluator_version: Identifier
    trace_id: TraceID | None = None
    span_id: SpanID | None = None
    session_id: Identifier | None = None
    score_type: Literal["boolean", "numeric", "categorical", "none"]
    score: bool | Decimal | Identifier | None = None
    outcome: Literal["passed", "failed", "error", "unknown"]
    error_code: Identifier | None = None
    observed_at: datetime

    @model_validator(mode="before")
    @classmethod
    def strict_score(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        value = data.get("score")
        kind = data.get("score_type")
        if kind == "numeric":
            if type(value) not in {int, float, str, Decimal} or len(str(value)) > 100:
                raise ValueError("NUMERIC_SCORE_REQUIRED")
            try:
                data["score"] = Decimal(str(value))
            except ArithmeticError:
                raise ValueError("NUMERIC_SCORE_INVALID") from None
        elif kind == "boolean" and type(value) is not bool:
            raise ValueError("BOOLEAN_SCORE_REQUIRED")
        elif kind == "categorical" and not isinstance(value, str):
            raise ValueError("CATEGORICAL_SCORE_REQUIRED")
        return data

    @model_validator(mode="after")
    def valid_result(self):
        if (self.trace_id is None) == (self.session_id is None) or (
            self.span_id and not self.trace_id
        ):
            raise ValueError("EXACTLY_ONE_TRACE_OR_SESSION_TARGET_REQUIRED")
        if (
            self.trace_id
            and not int(self.trace_id, 16)
            or self.span_id
            and not int(self.span_id, 16)
        ):
            raise ValueError("NONZERO_IDENTITIES_REQUIRED")
        valid = {
            "none": self.score is None,
            "boolean": type(self.score) is bool,
            "numeric": isinstance(self.score, Decimal),
            "categorical": isinstance(self.score, str),
        }
        if not valid[self.score_type] or (
            self.outcome in {"error", "unknown"} and self.score is not None
        ):
            raise ValueError("SCORE_SHAPE_INVALID")
        if isinstance(self.score, Decimal) and (
            not self.score.is_finite() or abs(self.score) > Decimal("1e20")
        ):
            raise ValueError("SCORE_RANGE_INVALID")
        if (self.outcome == "error") != (self.error_code is not None):
            raise ValueError("ERROR_CODE_REQUIRED_ONLY_FOR_ERROR")
        if self.observed_at.tzinfo is None or self.observed_at > datetime.now(UTC) + timedelta(
            minutes=5
        ):
            raise ValueError("OBSERVED_TIME_INVALID")
        self.observed_at = self.observed_at.astimezone(UTC)
        return self


class EvaluationBatch(StrictModel):
    profile: Literal["cutback.evaluation.v1"] = "cutback.evaluation.v1"
    results: list[EvaluationResult] = Field(min_length=1, max_length=200)


class NativeObservation(StrictModel):
    status: Literal["canonical_native", "canonical_external", "ambiguous", "not_observed"]
    observations: int
    observation_page_truncated: bool
    event_id: UUID | None
    cost_usd: str | None
    cost_status: str
    replay_supported: Literal[False] = False


class ObservedSpan(StrictModel):
    trace_id: TraceID
    span_id: SpanID
    parent_span_id: SpanID | None
    session_id: Identifier | None
    operation: Identifier
    kind: str
    service: Identifier
    workflow: Identifier | None
    release: Identifier | None
    # Strings preserve nanoseconds across JavaScript's 53-bit integer boundary.
    started_ns: str
    ended_ns: str | None
    status: str
    issues: list[str]
    native: NativeObservation | None
    content_available: bool = False
    content_id: UUID | None = None


class AssertedEvaluation(EvaluationResult):
    evidence: Literal[
        "externally_asserted", "cutback_model_judge", "cutback_technical_contract"
    ] = "externally_asserted"
    target_in_retained_page: bool | None = None
    source_status: Literal["missing"] | None = None


class TraceDetail(StrictModel):
    profile: Literal["cutback.workflow.v1"] = "cutback.workflow.v1"
    trace_id: TraceID
    environment_id: UUID
    spans: list[ObservedSpan]
    integrity: list[str]
    truncated: bool
    returned_spans: int
    observed_execution_errors: int
    business_success: Literal["unknown"] = "unknown"
    full_trace_cost_usd: None = None
    page_known_llm_cost_usd: str | None
    cost_coverage: Literal["partial_observations_only"] = "partial_observations_only"
    replay_supported: Literal[False] = False
    evaluations: list[AssertedEvaluation]
    evaluations_truncated: bool


class TraceSummary(StrictModel):
    id: UUID
    trace_id: TraceID
    environment_id: UUID
    received_at: datetime
    retained_spans: int
    observed_execution_errors: int
    capture_coverage: Literal["unknown"] = "unknown"


class TracePage(StrictModel):
    items: list[TraceSummary]
    next_cursor: UUID | None
    order: Literal["received_at_desc"] = "received_at_desc"
    profile: Literal["cutback.workflow.v1"] = "cutback.workflow.v1"


class SessionSummary(StrictModel):
    id: UUID
    session_id: Identifier
    received_at: datetime
    retained_traces: int
    observed_execution_errors: int


class SessionPage(StrictModel):
    items: list[SessionSummary]
    next_cursor: UUID | None
    order: Literal["received_at_desc"] = "received_at_desc"


class EvaluationPage(StrictModel):
    items: list[AssertedEvaluation]
    next_cursor: UUID | None


class TraceAlignment(StrictModel):
    operation_path: list[str]
    left_span_id: SpanID | None
    right_span_id: SpanID | None
    alignment: Literal["matched_path_occurrence", "left_only", "right_only"]


class TraceComparison(StrictModel):
    left: TraceDetail
    right: TraceDetail
    rows: list[TraceAlignment]
    semantics: Literal["exact_retained_operation_kind_ancestry_and_occurrence"]
    task_quality_comparison: Literal["not_verified"] = "not_verified"
