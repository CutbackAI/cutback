"""Explicitly projected OTLP JSON / OpenInference metadata profile v1."""

from fastapi import Depends, HTTPException
from pydantic import ValidationError

from cutback.core.traces import SpanBatch, WorkflowSpan
from cutback.server.otel import TraceExport, attributes

RESOURCE = {"service.name", "deployment.environment.name", "deployment.environment"}
SPAN = {
    "openinference.span.kind",
    "session.id",
    "cutback.operation",
    "cutback.workflow_id",
    "cutback.source_commit",
}
KINDS = {
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
}


def projected_attributes(items, allowed):
    selected = [item for item in items if item.key in allowed]
    return attributes(selected, allowed), len(items) - len(selected)


def project_export(body, environment):
    if sum(len(s.spans) for r in body.resourceSpans for s in r.scopeSpans) > 200:
        raise HTTPException(400, "OTEL_BATCH_EXCEEDS_200_SPANS")
    spans, rejected, projected = [], 0, 0
    for resource in body.resourceSpans:
        values, dropped = projected_attributes(resource.resource.attributes, RESOURCE)
        projected += dropped
        if any(
            values[k] != environment
            for k in ("deployment.environment", "deployment.environment.name")
            if k in values
        ):
            raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
        for scope in resource.scopeSpans:
            for span in scope.spans:
                try:
                    attrs, dropped = projected_attributes(span.attributes, SPAN)
                    projected += dropped
                    code = span.status.get("code", 0)
                    if type(code) is not int or code not in {0, 1, 2}:
                        raise ValueError("STATUS_INVALID")
                    kind = attrs.get("openinference.span.kind", "unknown").lower()
                    kind = kind if kind in KINDS else "unknown"
                    item = WorkflowSpan.model_validate(
                        dict(
                            trace_id=span.traceId.lower(),
                            span_id=span.spanId.lower(),
                            parent_span_id=span.parentSpanId.lower()
                            if span.parentSpanId and int(span.parentSpanId, 16)
                            else None,
                            session_id=attrs.get("session.id"),
                            operation=attrs.get("cutback.operation", kind),
                            kind=kind,
                            service=values.get("service.name", "external-otel"),
                            workflow=attrs.get("cutback.workflow_id"),
                            release=attrs.get("cutback.source_commit"),
                            started_ns=int(span.startTimeUnixNano),
                            ended_ns=int(span.endTimeUnixNano),
                            status={0: "unknown", 1: "ok", 2: "error"}[code],
                        )
                    )
                    SpanBatch(
                        spans=[item]
                    )  # Apply the same bounded arrival window as native metadata.
                    spans.append(item)
                except (ValueError, TypeError, OverflowError, ValidationError):
                    rejected += 1
    return spans, rejected, projected


def install_workflow_otel(app, sessions, authorize, ingest_scope, ingest_spans):
    @app.post("/v1/ingest/workflow-otel/v1/traces")
    def projected_traces(body: TraceExport, auth=Depends(authorize)):
        with sessions.begin() as session:
            project, env = ingest_scope(auth, session)
            try:
                spans, rejected, projected = project_export(body, env.name)
            except (ValueError, TypeError, OverflowError):
                raise HTTPException(400, "WORKFLOW_OTEL_PROFILE_INVALID") from None
            receipt = (
                ingest_spans(session, project, env.id, SpanBatch(spans=spans))
                if spans
                else {
                    "profile": "cutback.workflow.v1",
                    "accepted": 0,
                    "duplicates": 0,
                    "conflicts": 0,
                }
            )
            receipt.update(
                {
                    "projection": "openinference-metadata.v1",
                    "projected_attributes": projected,
                    "span_names_and_status_messages": "discarded",
                }
            )
            if rejected:
                receipt["partialSuccess"] = {
                    "rejectedSpans": str(rejected),
                    "errorMessage": "WORKFLOW_METADATA_INVALID",
                }
            return receipt
