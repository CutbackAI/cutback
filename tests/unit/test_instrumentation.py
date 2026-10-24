import asyncio
import json
from collections import Counter
from uuid import uuid4

import httpx
import openai
import pytest

import cutback
from cutback.instrumentation import workflow

SYNTHETIC_RESPONSE = {
    "id": "resp_synthetic",
    "object": "response",
    "created_at": 1,
    "status": "completed",
    "model": "gpt-4.1-mini-2025-04-14",
    "output": [],
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_tokens": 1100,
        "input_tokens_details": {"cached_tokens": 200},
        "output_tokens_details": {"reasoning_tokens": 20},
    },
}
SYNTHETIC_CHAT = {
    "id": "chatcmpl_synthetic",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-4.1-mini-2025-04-14",
    "choices": [],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "prompt_tokens_details": {"cached_tokens": 200},
        "completion_tokens_details": {"reasoning_tokens": 20},
    },
}


class MemoryExporter:
    def __init__(self, **kwargs):
        self.events = []
        self.counters = Counter()

    def emit(self, event):
        self.events.append(event)

    def count(self, key, amount=1):
        self.counters[key] += amount

    def health(self):
        return dict(self.counters)

    def flush(self, timeout_seconds=0.3):
        return True

    close = flush


@pytest.fixture
def observer(monkeypatch):
    monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "synthetic-token-" + "a" * 40)
    monkeypatch.setattr("cutback.instrumentation.openai.Exporter", MemoryExporter)
    obj = cutback.init(
        service_name="synthetic",
        environment="staging",
        enabled=True,
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
    )
    yield obj
    obj.close()


@pytest.mark.parametrize("surface", ["responses", "chat"])
@pytest.mark.parametrize("asynchronous", [False, True])
def test_sdk_nonstream_native_result_usage_privacy(observer, surface, asynchronous):
    payload = SYNTHETIC_RESPONSE if surface == "responses" else SYNTHETIC_CHAT
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    async def run_async():
        async with openai.AsyncOpenAI(
            api_key="synthetic-provider-key",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        ) as client:
            if surface == "responses":
                return await client.responses.create(model="gpt-4.1-mini", input="PRIVATE RESUME")
            return await client.chat.completions.create(
                model="gpt-4.1-mini", messages=[{"role": "user", "content": "PRIVATE RESUME"}]
            )

    with workflow("workflow:synthetic", call_site_id="callsite:synthetic"):
        if asynchronous:
            result = asyncio.run(run_async())
        else:
            with openai.OpenAI(
                api_key="synthetic-provider-key",
                max_retries=0,
                http_client=httpx.Client(transport=transport),
            ) as client:
                result = (
                    client.responses.create(model="gpt-4.1-mini", input="PRIVATE RESUME")
                    if surface == "responses"
                    else client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[{"role": "user", "content": "PRIVATE RESUME"}],
                    )
                )
    (event,) = observer.exporter.events
    assert result.id == payload["id"]
    assert event.input_tokens == 1000 and event.output_tokens == 100
    assert event.cache_read_tokens == 200 and event.reasoning_tokens == 20
    assert event.workflow_id == "workflow:synthetic"
    assert event.metering_status == "text_only"
    assert event.usage_source == "provider_reported"
    assert "PRIVATE" not in event.model_dump_json()
    assert "provider-key" not in event.model_dump_json()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("surface", ["responses", "chat"])
def test_native_stream_one_event(observer, asynchronous, surface):
    if surface == "responses":
        chunks = [
            {"type": "response.completed", "sequence_number": 0, "response": SYNTHETIC_RESPONSE}
        ]
    else:
        chunks = [{**SYNTHETIC_CHAT, "object": "chat.completion.chunk"}]
    data = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=data, headers={"content-type": "text/event-stream"}
        )
    )

    async def run_async():
        async with openai.AsyncOpenAI(
            api_key="synthetic", max_retries=0, http_client=httpx.AsyncClient(transport=transport)
        ) as client:
            stream = (
                await client.responses.create(model="gpt-4.1-mini", input="private", stream=True)
                if surface == "responses"
                else await client.chat.completions.create(
                    model="gpt-4.1-mini", messages=[], stream=True
                )
            )
            assert isinstance(stream, openai.AsyncStream)
            async with stream:
                received = [chunk async for chunk in stream]
            assert len(received) == 1

    if asynchronous:
        asyncio.run(run_async())
    else:
        with openai.OpenAI(
            api_key="synthetic", max_retries=0, http_client=httpx.Client(transport=transport)
        ) as client:
            stream = (
                client.responses.create(model="gpt-4.1-mini", input="private", stream=True)
                if surface == "responses"
                else client.chat.completions.create(model="gpt-4.1-mini", messages=[], stream=True)
            )
            assert isinstance(stream, openai.Stream)
            with stream:
                assert len(list(stream)) == 1
    (event,) = observer.exporter.events
    assert event.status == "success"
    assert event.input_tokens == 1000


def test_stream_without_usage_and_early_close(observer):
    chunk = {**SYNTHETIC_CHAT, "usage": None, "object": "chat.completion.chunk"}
    data = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=data, headers={"content-type": "text/event-stream"}
        )
    )
    with openai.OpenAI(
        api_key="synthetic", http_client=httpx.Client(transport=transport)
    ) as client:
        with client.chat.completions.create(
            model="gpt-4.1-mini", messages=[], stream=True
        ) as stream:
            next(stream)
    (event,) = observer.exporter.events
    assert event.status == "incomplete"
    assert event.input_tokens is None and event.usage_source == "unavailable"


def test_provider_error_rethrown_unchanged_no_sensitive_error_message(observer):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            401,
            json={"error": {"message": "PRIVATE SECRET PROVIDER KEY", "type": "invalid_api_key"}},
        )
    )
    with openai.OpenAI(
        api_key="synthetic", max_retries=0, http_client=httpx.Client(transport=transport)
    ) as client:
        with pytest.raises(openai.AuthenticationError, match="PRIVATE SECRET"):
            client.responses.create(model="gpt-4.1-mini", input="PRIVATE")
    (event,) = observer.exporter.events
    assert event.status == "error" and event.http_status == 401
    assert event.usage_source == "unavailable"
    assert "PRIVATE" not in event.model_dump_json()


def test_normalization_failure_does_not_change_response(observer):
    payload = {**SYNTHETIC_RESPONSE, "usage": {"input_tokens": -1, "output_tokens": 100}}
    with openai.OpenAI(
        api_key="synthetic",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
        ),
    ) as client:
        result = client.responses.create(model="gpt-4.1-mini", input="PRIVATE")
    assert result.id == "resp_synthetic"
    assert not observer.exporter.events
    assert observer.exporter.counters["normalization_failed"] == 1


def test_disabled_no_patching_and_content_capture_refused(monkeypatch):
    from openai.resources.responses import Responses

    before = Responses.create
    assert not cutback.init(service_name="x", environment="x", enabled=False).enabled
    assert Responses.create is before
    with pytest.raises(ValueError, match="content capture"):
        cutback.init(service_name="x", environment="x", capture_content=True)
