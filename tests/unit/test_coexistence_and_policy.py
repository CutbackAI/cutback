import asyncio
import functools
from uuid import uuid4

import httpx
import openai
import pytest
from test_instrumentation import SYNTHETIC_RESPONSE, MemoryExporter

import cutback
from cutback.evals.datasets import redact
from cutback.instrumentation.serverless import instrument_handler


def test_overlapping_sdk_wrapper_is_refused_and_otel_context_is_preserved(monkeypatch):
    from openai.resources.responses import Responses
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext

    original = Responses.create
    calls = []

    @functools.wraps(original)
    def existing_wrapper(*args, **kwargs):
        calls.append(trace.get_current_span().get_span_context().trace_id)
        return original(*args, **kwargs)

    monkeypatch.setattr(Responses, "create", existing_wrapper)
    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "synthetic-" + "x" * 40)
    with pytest.raises(ValueError, match="existing SDK wrapper detected"):
        cutback.init(
            service_name="synthetic-coexistence",
            environment="staging",
            enabled=True,
            project_id=str(uuid4()),
            endpoint="http://127.0.0.1:8080/v1/ingest/events",
        )
    assert Responses.create is existing_wrapper
    monkeypatch.setattr(Responses, "create", original)
    observer = cutback.init(
        service_name="synthetic-coexistence",
        environment="staging",
        enabled=True,
        project_id=str(uuid4()),
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
    )
    context = SpanContext(trace_id=1234, span_id=5678, is_remote=False)
    try:
        with (
            trace.use_span(NonRecordingSpan(context)),
            openai.OpenAI(
                api_key="synthetic-key",
                http_client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json=SYNTHETIC_RESPONSE)
                    )
                ),
            ) as client,
        ):
            native = client.responses.create(model="gpt-4.1-mini", input="owned synthetic fixture")
        assert native.id == SYNTHETIC_RESPONSE["id"] and calls == []
        event = observer.exporter.events[0]
        assert event.trace_id == f"{1234:032x}" and event.parent_span_id == f"{5678:016x}"
    finally:
        observer.close()
    assert Responses.create is original


@pytest.mark.parametrize("asynchronous", [False, True])
def test_serverless_shutdown_respects_remaining_time_and_preserves_failure(asynchronous):
    class Context:
        def get_remaining_time_in_millis(self):
            return 10

    class Observer:
        deadlines = []

        def flush(self, timeout):
            self.deadlines.append(timeout)
            raise OSError("synthetic outage")

    def handler(event, context):
        raise LookupError("owned application error")

    async def async_handler(event, context):
        return handler(event, context)

    observer = Observer()
    observer.deadlines = []
    wrapped = instrument_handler(observer)(async_handler if asynchronous else handler)
    with pytest.raises(LookupError, match="owned application error"):
        if asynchronous:
            asyncio.run(wrapped({}, Context()))
        else:
            wrapped({}, Context())
    assert observer.deadlines == [0]


def test_denied_fields_inside_json_outputs_cannot_bypass_policy():
    value = {"output": '{"safe":"retained","forbidden":"secret fixture"}'}
    result = redact(value, ["forbidden"])
    assert "secret fixture" not in str(result) and "retained" in str(result)
