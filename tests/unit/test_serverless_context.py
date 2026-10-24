import asyncio
from unittest.mock import Mock

import httpx
import openai
import pytest
from opentelemetry import trace
from test_instrumentation import SYNTHETIC_RESPONSE, observer  # noqa: F401, F811

from cutback.instrumentation.serverless import instrument_handler


@pytest.mark.parametrize("asynchronous", [False, True])
def test_bounded_handler_flush_success_failure_and_handler_exception(asynchronous):
    monitor = Mock()
    monitor.flush.side_effect = RuntimeError("synthetic broken exporter")
    if asynchronous:

        @instrument_handler(monitor, max_flush_seconds=0.1)
        async def handler(value):
            if value:
                raise ValueError("original application error")
            return "original result"

        assert asyncio.run(handler(False)) == "original result"
        with pytest.raises(ValueError, match="original application error"):
            asyncio.run(handler(True))
    else:

        @instrument_handler(monitor, max_flush_seconds=0.1)
        def handler(value):
            if value:
                raise ValueError("original application error")
            return "original result"

        assert handler(False) == "original result"
        with pytest.raises(ValueError, match="original application error"):
            handler(True)
    assert monitor.flush.call_count == 2
    monitor.flush.assert_called_with(0.1)


def test_otel_parent_context_without_global_provider(observer):  # noqa: F811
    context = trace.SpanContext(
        trace_id=int("a" * 32, 16), span_id=int("b" * 16, 16), is_remote=False
    )
    parent = trace.NonRecordingSpan(context)
    with (
        trace.use_span(parent),
        openai.OpenAI(
            api_key="synthetic",
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=SYNTHETIC_RESPONSE)
                )
            ),
        ) as client,
    ):
        client.responses.create(model="gpt-4.1-mini", input="private")
    (event,) = observer.exporter.events
    assert event.trace_id == "a" * 32
    assert event.parent_span_id == "b" * 16
    assert event.span_id != event.parent_span_id
