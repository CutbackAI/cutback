"""Run separately against the pilot's exact SDK pin. No live provider calls."""

import asyncio
import importlib.metadata
from uuid import uuid4

import httpx
import openai
import pytest

import cutback

pytestmark = pytest.mark.skipif(
    importlib.metadata.version("openai") != "1.55.3",
    reason="separate legacy SDK compatibility environment",
)

CHAT = {
    "id": "chatcmpl_synthetic",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4.1-mini",
    "choices": [],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 0},
    },
}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_legacy_native_result_stream_retry_and_privacy(monkeypatch, asynchronous, stream):
    import cutback.instrumentation.openai as adapter

    class Memory:
        def __init__(self, **kwargs):
            self.events = []

        def emit(self, event):
            self.events.append(event)

        def count(self, *args):
            pass

        def close(self, *args):
            return True

    monkeypatch.setattr(adapter, "Exporter", Memory)
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "a" * 48)
    monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
    from openai._base_client import BaseClient

    monkeypatch.setattr(BaseClient, "_calculate_retry_timeout", lambda *a, **kw: 0)
    observer = cutback.init(
        service_name="legacy-synthetic",
        environment="staging",
        enabled=True,
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
    )
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(500, json={"error": {"message": "synthetic"}})
        if not stream:
            return httpx.Response(200, json=CHAT)
        payload = f"data: {__import__('json').dumps(CHAT)}\n\ndata: [DONE]\n\n"
        return httpx.Response(200, content=payload, headers={"content-type": "text/event-stream"})

    async def invoke():
        async with openai.AsyncOpenAI(
            api_key="synthetic",
            max_retries=1,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ) as client:
            result = await client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "PRIVATE"}],
                stream=stream,
            )
            if stream:
                assert len([chunk async for chunk in result]) == 1
            else:
                assert result.id == "chatcmpl_synthetic"

    try:
        if asynchronous:
            asyncio.run(invoke())
        else:
            with openai.OpenAI(
                api_key="synthetic",
                max_retries=1,
                http_client=httpx.Client(transport=httpx.MockTransport(handler)),
            ) as client:
                result = client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": "PRIVATE"}],
                    stream=stream,
                )
                if stream:
                    assert len(list(result)) == 1
                else:
                    assert result.id == "chatcmpl_synthetic"
        assert len(calls) == 2
        first, second = observer.exporter.events
        assert first.retry_number == 0 and first.status == "error" and first.http_status == 500
        assert second.retry_number == 1 and second.input_tokens == 100
        assert first.logical_request_id == second.logical_request_id
        assert "PRIVATE" not in first.model_dump_json() + second.model_dump_json()
    finally:
        observer.close()
