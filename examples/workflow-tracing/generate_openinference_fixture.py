"""Regenerate using the exact optional producer versions in README; sends no traffic."""

import base64
import json
from importlib.metadata import version
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from openinference.semconv.trace import OpenInferenceSpanKindValues, SpanAttributes
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator


class SyntheticIDs(IdGenerator):
    current = 0

    def generate_span_id(self):
        self.current += 1
        return self.current

    def generate_trace_id(self):
        return int("a" * 32, 16)


def main():
    expected = {
        "opentelemetry-sdk": "1.45.1",
        "opentelemetry-proto": "1.45.1",
        "opentelemetry-exporter-otlp-proto-common": "1.45.1",
        "openinference-semantic-conventions": "0.1.42",
    }
    assert all(version(k) == v for k, v in expected.items()), "Use pinned producer versions"
    exporter = InMemorySpanExporter()
    provider = TracerProvider(
        id_generator=SyntheticIDs(),
        resource=Resource(
            attributes={
                "service.name": "synthetic-support",
                "deployment.environment.name": "staging",
            }
        ),
    )
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("cutback.synthetic.openinference", "1")
    start = 1793131200000000000
    attrs = {
        SpanAttributes.SESSION_ID: "synthetic-session",
        "cutback.workflow_id": "support.answer",
    }
    with tracer.start_as_current_span(
        "Synthetic user request — discarded", start_time=start, end_on_exit=False
    ) as root:
        root.set_attributes(
            {
                **attrs,
                SpanAttributes.OPENINFERENCE_SPAN_KIND: OpenInferenceSpanKindValues.AGENT.value,
                "cutback.operation": "support.answer",
            }
        )
        for i, (kind, operation) in enumerate(
            [
                (OpenInferenceSpanKindValues.RETRIEVER, "retrieve.policy"),
                (OpenInferenceSpanKindValues.TOOL, "lookup.order"),
                (OpenInferenceSpanKindValues.LLM, "compose.answer"),
            ]
        ):
            child = tracer.start_span(
                "Synthetic content — discarded", start_time=start + (i + 1) * 100_000_000
            )
            child.set_attributes(
                {
                    **attrs,
                    SpanAttributes.OPENINFERENCE_SPAN_KIND: kind.value,
                    "cutback.operation": operation,
                    SpanAttributes.INPUT_VALUE: "SYNTHETIC_CONTENT_NOT_RETAINED",
                }
            )
            child.end(end_time=start + (i + 2) * 100_000_000)
        root.end(end_time=start + 500_000_000)
    data = MessageToDict(encode_spans(exporter.get_finished_spans()), use_integers_for_enums=True)
    # OTLP JSON uses hex identifiers, unlike standard protobuf JSON's base64 bytes.
    for resource in data["resourceSpans"]:
        for scope in resource["scopeSpans"]:
            for span in scope["spans"]:
                for key in ("traceId", "spanId", "parentSpanId"):
                    if key in span:
                        span[key] = base64.b64decode(span[key]).hex()
    destination = Path(__file__).with_name("openinference-v1.json")
    destination.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    Path(__file__).with_name("producer-versions.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )
    provider.shutdown()
    print("Generated four synthetic SDK spans; no exporter network transport configured.")


if __name__ == "__main__":
    main()
