"""Typed metadata chart definitions; no SQL, scripts or customer content."""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.server.prompt_contracts import EnvironmentName

Metric = Literal[
    "observed_attempts",
    "known_modeled_cost_subtotal_usd",
    "errors",
    "latency_p95_ms",
    "input_tokens_observed",
    "output_tokens_observed",
]
Group = Literal["day", "model", "provider", "workflow", "traffic"]
Traffic = Literal["application", "evaluation", "judge", "optimization", "simulation", "unknown"]
Unit = Literal["attempts", "USD", "ms", "tokens"]
Revision = Annotated[StrictInt, Field(ge=1, le=2147483647)]
Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class AnalyticsPanel(StrictModel):
    key: Identifier
    metric: Metric
    chart: Literal["stat", "bar", "line", "table"] = "bar"
    group_by: Group = "day"
    traffic_kind: Traffic | None = None
    workflow: Identifier | None = None

    @model_validator(mode="after")
    def chronological_lines(self):
        if self.chart == "line" and self.group_by != "day":
            raise ValueError("ANALYTICS_LINE_REQUIRES_TIME_GROUPING")
        return self


class AnalyticsInput(StrictModel):
    dashboard_id: UUID
    key: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0, le=2147483646)]
    environment: EnvironmentName
    window_days: Annotated[StrictInt, Field(ge=1, le=365)] = 7
    panels: list[AnalyticsPanel] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def bounded_identity(self):
        if not self.dashboard_id.int or self.window_days not in {1, 7, 30, 90, 365}:
            raise ValueError("ANALYTICS_IDENTITY_OR_WINDOW_INVALID")
        keys = [panel.key for panel in self.panels]
        if len(set(keys)) != len(keys):
            raise ValueError("ANALYTICS_DISTINCT_PANEL_KEYS_REQUIRED")
        return self


class AnalyticsDefinition(StrictModel):
    version: Literal["cutback.analytics-dashboard.v1"] = "cutback.analytics-dashboard.v1"
    page: Literal["analytics_dashboard"] = "analytics_dashboard"
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: EnvironmentName
    window_days: Annotated[StrictInt, Field(ge=1, le=365)]
    panels: list[AnalyticsPanel] = Field(min_length=1, max_length=8)


class AnalyticsSummary(StrictModel):
    id: UUID
    key: Identifier
    revision: Revision
    environment: EnvironmentName
    window_days: StrictInt
    panel_count: Annotated[StrictInt, Field(ge=1, le=8)]
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class AnalyticsDetail(AnalyticsSummary):
    panels: list[AnalyticsPanel] = Field(min_length=1, max_length=8)
    content_included: Literal[False] = False


class AnalyticsPage(StrictModel):
    items: list[AnalyticsSummary] = Field(max_length=100)
    next_cursor: UUID | None = None


class AnalyticsPoint(StrictModel):
    key: str = Field(max_length=240)
    value: Amount | None
    observed_attempts: Annotated[StrictInt, Field(ge=0)]
    unpriced_attempts: Annotated[StrictInt, Field(ge=0)] | None
    missing_usage_attempts: Annotated[StrictInt, Field(ge=0)] | None
    timed_attempts: Annotated[StrictInt, Field(ge=0)] | None
    modeled_cost_usd: Amount | None
    known_modeled_cost_subtotal_usd: Amount | None


class AnalyticsPanelData(StrictModel):
    key: Identifier
    metric: Metric
    chart: Literal["stat", "bar", "line", "table"]
    group_by: Group
    unit: Unit
    metric_semantics: str = Field(max_length=240)
    summary: AnalyticsPoint
    points: list[AnalyticsPoint] = Field(max_length=365)
    points_truncated: bool
    archived_daily_rows: Annotated[StrictInt, Field(ge=0)]
    partial_days_excluded: list[str] = Field(max_length=367)
    population_coverage: Literal["unknown"] = "unknown"
    content_included: Literal[False] = False


class AnalyticsData(StrictModel):
    dashboard_id: UUID
    revision: Revision
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: EnvironmentName
    generated_at: AwareDatetime
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    panels: list[AnalyticsPanelData] = Field(min_length=1, max_length=8)
    population_coverage: Literal["unknown"] = "unknown"
    cost_evidence: Literal["recorded_usage_modeled_not_invoice"] = (
        "recorded_usage_modeled_not_invoice"
    )
    content_included: Literal[False] = False
