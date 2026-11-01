"""Shared metadata dashboards pin typed view revisions; they never store query code."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from cutback.core.models import Identifier, StrictModel
from cutback.server.monitoring_contracts import SpanFilter
from cutback.server.prompt_contracts import EnvironmentName

Revision = Annotated[StrictInt, Field(ge=1, le=2147483647)]


class DashboardViewRef(StrictModel):
    view_id: UUID
    revision: Revision


class DashboardInput(StrictModel):
    dashboard_id: UUID
    key: Identifier
    expected_revision: Annotated[StrictInt, Field(ge=0, le=2147483646)]
    environment: EnvironmentName
    panels: list[DashboardViewRef] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def distinct_views(self):
        identities = [value.view_id for value in self.panels]
        if not self.dashboard_id.int or any(not value.int for value in identities):
            raise ValueError("DASHBOARD_NONZERO_IDENTITIES_REQUIRED")
        if len(set(identities)) != len(identities):
            raise ValueError("DASHBOARD_DISTINCT_VIEWS_REQUIRED")
        return self


class DashboardPanel(StrictModel):
    view_id: UUID
    revision: Revision
    key: Identifier
    page: Literal["traces", "incidents"]
    environment: EnvironmentName
    filter: SpanFilter
    incident_state: Literal["open", "acknowledged", "resolved"] | None = None


class DashboardDefinition(StrictModel):
    version: Literal["cutback.shared-dashboard.v1"] = "cutback.shared-dashboard.v1"
    page: Literal["dashboard"] = "dashboard"
    environment: EnvironmentName
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    panels: list[DashboardPanel] = Field(min_length=1, max_length=8)


class DashboardSummary(StrictModel):
    id: UUID
    key: Identifier
    revision: Revision
    environment: EnvironmentName
    panel_count: Annotated[StrictInt, Field(ge=1, le=8)]
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class DashboardPanelDetail(DashboardPanel):
    source_state: Literal["current", "changed", "deleted"]


class DashboardDetail(DashboardSummary):
    panels: list[DashboardPanelDetail] = Field(min_length=1, max_length=8)
    content_included: Literal[False] = False
    population_coverage: Literal["unknown"] = "unknown"


class DashboardPage(StrictModel):
    items: list[DashboardSummary] = Field(max_length=100)
    next_cursor: UUID | None = None


class DashboardEvidence(StrictModel):
    id: UUID
    kind: Literal["span", "incident"]
    trace_record_id: UUID | None = None
    span_id: str | None = None
    workflow: Identifier | None = None
    service: Identifier | None = None
    release: Identifier | None = None
    span_kind: str | None = None
    status: str | None = None
    started_ns: str | None = None
    incident_state: Literal["open", "acknowledged", "resolved"] | None = None
    owner: Identifier | None = None


class DashboardEvidencePage(StrictModel):
    items: list[DashboardEvidence] = Field(max_length=50)
    next_cursor: UUID | None = None
    selection_basis: Literal["matching_retained_spans", "matching_incident_rule_filters"]
    retention_coverage: Literal["unknown"] = "unknown"
    population_coverage: Literal["unknown"] = "unknown"
    content_included: Literal[False] = False
