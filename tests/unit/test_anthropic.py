import asyncio
import copy
import json
from uuid import uuid4

import anthropic
import httpx2
import pytest
from test_instrumentation import MemoryExporter

import cutback

MESSAGE = {
    "id": "msg_synthetic",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "PRIVATE OUTPUT"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 50,
        "cache_creation_input_tokens": 15,
        "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 5},
    },
}


@pytest.mark.parametrize(
    "detail",
    [
        {"ephemeral_1h_input_tokens": 2},
        {"ephemeral_5m_input_tokens": 1},
        {"ephemeral_1h_input_tokens": False},
        None,
        {"ephemeral_5m_input_tokens": 0},
    ],
)
def test_zero_aggregate_cannot_erase_inconsistent_native_duration_counts(monkeypatch, detail):
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "a" * 48)
    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    observer = cutback.init(
        service_name="synthetic-cache-partition",
        environment="staging",
        enabled=True,
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        providers=("anthropic",),
    )
    wire = copy.deepcopy(MESSAGE)
    wire["usage"].update(cache_creation_input_tokens=0, cache_creation=detail)
    try:
        with anthropic.Anthropic(
            api_key="synthetic",
            max_retries=0,
            http_client=httpx2.Client(
                transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=wire))
            ),
        ) as client:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=32,
                messages=[{"role": "user", "content": "SYNTHETIC_PRIVATE_INPUT"}],
            )
        assert response.id == wire["id"]
        (event,) = observer.exporter.events
        if detail is None or detail == {"ephemeral_5m_input_tokens": 0}:
            assert event.cache_write_tokens == event.cache_write_1h_tokens == 0
        else:
            assert event.usage_source == "unavailable" and event.input_tokens is None
        assert "PRIVATE" not in event.model_dump_json()
    finally:
        observer.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_messages_sync_async_native_stream_and_cache_usage(monkeypatch, asynchronous, stream):
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "a" * 48)
    monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    observer = cutback.init(
        service_name="synthetic",
        environment="staging",
        enabled=True,
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        providers=("anthropic",),
    )

    def handler(request):
        if not stream:
            return httpx2.Response(200, json=MESSAGE)
        events = [
            {"type": "message_start", "message": {**MESSAGE, "content": []}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 20},
            },
            {"type": "message_stop"},
        ]
        data = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx2.Response(200, content=data, headers={"content-type": "text/event-stream"})

    async def invoke():
        async with anthropic.AsyncAnthropic(
            api_key="synthetic",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        ) as client:
            result = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=32,
                messages=[{"role": "user", "content": "PRIVATE INPUT"}],
                stream=stream,
            )
            if stream:
                assert isinstance(result, anthropic.AsyncStream)
                assert [chunk.type async for chunk in result][-1] == "message_stop"
            else:
                assert result.content[0].text == "PRIVATE OUTPUT"

    try:
        if asynchronous:
            asyncio.run(invoke())
        else:
            with anthropic.Anthropic(
                api_key="synthetic",
                max_retries=0,
                http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
            ) as client:
                result = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=32,
                    messages=[{"role": "user", "content": "PRIVATE INPUT"}],
                    stream=stream,
                )
                if stream:
                    assert isinstance(result, anthropic.Stream)
                    assert [chunk.type for chunk in result][-1] == "message_stop"
                else:
                    assert result.content[0].text == "PRIVATE OUTPUT"
        (event,) = observer.exporter.events
        assert event.provider == "anthropic" and event.input_tokens == 100
        assert event.cache_write_tokens == 10 and event.cache_write_1h_tokens == 5
        assert event.cache_read_tokens == 50 and event.output_tokens == 20
        assert "PRIVATE" not in event.model_dump_json()
        from decimal import Decimal

        from cutback.pricing import Catalog

        assert Catalog().price(event).amount == Decimal("0.0006825")
    finally:
        observer.close()
