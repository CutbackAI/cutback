"""Bounded OTLP/HTTP JSON GenAI metadata profile; never retains arbitrary attributes."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID, uuid5

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from cutback.core.models import EventBatch, LLMEvent
from cutback.server.db import Environment

RESOURCE_KEYS = {"service.name", "deployment.environment.name", "deployment.environment"}
SPAN_KEYS = {
    "gen_ai.system",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.operation.name",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "http.response.status_code",
    "cutback.sdk_surface",
    "cutback.usage_source",
    "cutback.metering_status",
    "cutback.workflow_id",
    "cutback.call_site_id",
    "cutback.traffic_kind",
    "cutback.cache_read_tokens",
    "cutback.cache_write_tokens",
    "cutback.cache_write_1h_tokens",
    "cutback.reasoning_tokens",
    "cutback.result_status",
    "cutback.source_commit",
    "cutback.logical_request_id",
}
NanoTime = (
    Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
    | Annotated[int, Field(strict=True, ge=0, le=2**64 - 1)]
)


class Attribute(BaseModel):
    key: str = Field(max_length=100)
    value: dict[str, Any] = Field(max_length=1)


class Resource(BaseModel):
    attributes: list[Attribute] = Field(default_factory=list, max_length=64)


class Span(BaseModel):
    traceId: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{32}$")]
    spanId: Annotated[str, StringConstraints(pattern=r"^[a-fA-F0-9]{16}$")]
    parentSpanId: Annotated[str, StringConstraints(pattern=r"^(?:[a-fA-F0-9]{16})?$")] | None = None
    startTimeUnixNano: NanoTime
    endTimeUnixNano: NanoTime
    attributes: list[Attribute] = Field(default_factory=list, max_length=64)
    events: list = Field(default_factory=list, max_length=0)
    links: list = Field(default_factory=list, max_length=0)
    status: dict[str, Any] = Field(default_factory=dict, max_length=2)


class ScopeSpans(BaseModel):
    spans: list[Span] = Field(default_factory=list, max_length=200)


class ResourceSpans(BaseModel):
    resource: Resource = Field(default_factory=Resource)
    scopeSpans: list[ScopeSpans] = Field(default_factory=list, max_length=20)


class TraceExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resourceSpans: list[ResourceSpans] = Field(default_factory=list, max_length=20)


def attributes(values, allowed):
    result: dict[str, Any] = {}
    for item in values:
        if item.key not in allowed or item.key in result or len(item.value) != 1:
            raise ValueError("OTEL_METADATA_PROFILE_VIOLATION")
        kind, value = next(iter(item.value.items()))
        if kind == "stringValue" and isinstance(value, str) and len(value) <= 200:
            result[item.key] = value
        elif kind == "intValue" and not isinstance(value, bool):
            if isinstance(value, str) and (
                not value.isascii() or not value.isdecimal() or len(value) > 20
            ):
                raise ValueError("OTEL_INTEGER_INVALID")
            if not isinstance(value, (str, int)):
                raise ValueError("OTEL_INTEGER_INVALID")
            result[item.key] = int(value)
        else:
            raise ValueError("OTEL_ATTRIBUTE_TYPE_UNSUPPORTED")
    return result


def convert(body, project_id, environment):
    events, rejected = [], 0
    count = sum(
        len(scope.spans) for resource in body.resourceSpans for scope in resource.scopeSpans
    )
    if count > 200:
        raise ValueError("OTEL_BATCH_EXCEEDS_200_SPANS")
    for resource in body.resourceSpans:
        identity = attributes(resource.resource.attributes, RESOURCE_KEYS)
        reported_environment = identity.get(
            "deployment.environment.name", identity.get("deployment.environment", environment)
        )
        if reported_environment != environment:
            raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
        for scope in resource.scopeSpans:
            for span in scope.spans:
                status = span.status.get("code", 0)
                if type(status) is not int or status not in {0, 1, 2}:
                    raise ValueError("OTEL_STATUS_ENUM_INVALID")
                values = attributes(span.attributes, SPAN_KEYS)
                provider = values.get("gen_ai.provider.name", values.get("gen_ai.system"))
                if values.get("gen_ai.system", provider) != provider:
                    raise ValueError("OTEL_PROVIDER_CONFLICT")
                if provider not in {"openai", "anthropic"}:
                    rejected += 1
                    continue
                surface = values.get("cutback.sdk_surface")
                if surface not in (
                    {"responses.create", "chat.completions.create"}
                    if provider == "openai"
                    else {"messages.create"}
                ):
                    raise ValueError("OTEL_SDK_SURFACE_REQUIRES_EXPLICIT_SUPPORTED_VALUE")
                start, end = int(span.startTimeUnixNano), int(span.endTimeUnixNano)
                if (
                    not 0 < start <= end <= 2**64 - 1
                    or not int(span.traceId, 16)
                    or not int(span.spanId, 16)
                ):
                    raise ValueError("OTEL_SPAN_TIME_OR_ID_INVALID")
                occurred = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=start // 1000)
                now = datetime.now(UTC)
                if not now - timedelta(days=30) <= occurred <= now + timedelta(minutes=5):
                    raise ValueError("OTEL_TIMESTAMP_OUT_OF_RANGE")
                tokens = {
                    field: values.get(key)
                    for field, key in (
                        ("input_tokens", "gen_ai.usage.input_tokens"),
                        ("output_tokens", "gen_ai.usage.output_tokens"),
                        ("cache_read_tokens", "cutback.cache_read_tokens"),
                        ("cache_write_tokens", "cutback.cache_write_tokens"),
                        ("cache_write_1h_tokens", "cutback.cache_write_1h_tokens"),
                        ("reasoning_tokens", "cutback.reasoning_tokens"),
                    )
                }
                source = values.get("cutback.usage_source", "unavailable")
                if source == "unavailable":
                    tokens = {key: None for key in tokens}
                identifier = uuid5(
                    project_id, "external-otel:" + span.traceId.lower() + ":" + span.spanId.lower()
                )
                events.append(
                    LLMEvent(
                        event_id=identifier,
                        request_attempt_id=identifier,
                        logical_request_id=values.get("cutback.logical_request_id"),
                        occurred_at=occurred,
                        environment=environment,
                        service_name=identity.get("service.name", "external-otel"),
                        sdk_version="external-otel-v1",
                        trace_id=span.traceId.lower(),
                        span_id=span.spanId.lower(),
                        parent_span_id=span.parentSpanId.lower()
                        if span.parentSpanId and int(span.parentSpanId, 16)
                        else None,
                        provider=provider,
                        sdk_surface=values.get("cutback.sdk_surface"),
                        requested_model=values.get("gen_ai.request.model"),
                        response_model=values.get("gen_ai.response.model"),
                        duration_ms=(end - start) / 1000000,
                        status=values.get(
                            "cutback.result_status",
                            "error" if span.status.get("code") == 2 else "success",
                        ),
                        http_status=values.get("http.response.status_code"),
                        workflow_id=values.get("cutback.workflow_id"),
                        call_site_id=values.get("cutback.call_site_id", "unattributed"),
                        traffic_kind=values.get("cutback.traffic_kind", "unknown"),
                        source_commit=values.get("cutback.source_commit"),
                        usage_source=source,
                        metering_status=values.get("cutback.metering_status", "unknown"),
                        sampling_policy_id="external-otel-spans-v1",
                        **tokens,
                    )
                )
    return events, rejected


def install_otel(app, sessions, authorize, scope, repository):
    @app.post("/v1/ingest/otel/v1/traces")
    def ingest(body: TraceExport, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        project = UUID(str(auth["project_id"]))
        scope(auth, project, ("ingest",))
        with sessions() as session:
            environment = session.get(Environment, auth["environment_id"])
            if not environment or environment.project_id != project:
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
            try:
                events, rejected = convert(body, project, environment.name)
            except (ValueError, TypeError, OverflowError):
                raise HTTPException(400, "OTEL_METADATA_PROFILE_INVALID") from None
            if events:
                repository.ingest_batch(
                    session,
                    EventBatch(project_id=project, events=events),
                    {environment.name: environment.id},
                )
        return (
            {
                "partialSuccess": {
                    "rejectedSpans": str(rejected),
                    "errorMessage": "UNSUPPORTED_PROVIDER_OR_NON_GENAI_SPAN",
                }
            }
            if rejected
            else {}
        )
