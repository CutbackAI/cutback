"""OpenAI 3's actual default transport, parsed responses and stream cancellation."""

import asyncio
import copy
import json
from importlib.metadata import version

import httpx2
import openai
import pytest
from test_instrumentation import SYNTHETIC_CHAT, SYNTHETIC_RESPONSE
from test_instrumentation import observer as observer  # pytest fixture

from cutback.pricing import Catalog

pytestmark = pytest.mark.skipif(version("openai") != "3.35.1", reason="separate OpenAI 3 matrix")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("surface", ["responses", "chat"])
@pytest.mark.parametrize("writes", [None, 0, 30])
def test_native_httpx2_preserves_response_usage_unknowns_and_retry_identity(
    observer, asynchronous, surface, writes
):
    payload = copy.deepcopy(SYNTHETIC_RESPONSE if surface == "responses" else SYNTHETIC_CHAT)
    details = payload["usage"][
        "input_tokens_details" if surface == "responses" else "prompt_tokens_details"
    ]
    if writes is not None:
        details["cache_write_tokens"] = writes
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx2.Response(
                503, headers={"retry-after-ms": "1"}, json={"error": {"message": "PRIVATE"}}
            )
        return httpx2.Response(200, json=payload)

    kwargs = (
        {"input": "PRIVATE"}
        if surface == "responses"
        else {"messages": [{"role": "user", "content": "PRIVATE"}]}
    )

    async def run():
        async with openai.AsyncOpenAI(
            api_key="synthetic-key",
            max_retries=1,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            return await resource.create(model="gpt-4.1-mini", **kwargs)

    if asynchronous:
        result = asyncio.run(run())
    else:
        with openai.OpenAI(
            api_key="synthetic-key",
            max_retries=1,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            result = resource.create(model="gpt-4.1-mini", **kwargs)
    assert result.id == payload["id"] and len(requests) == 2
    assert result.__class__.__module__.startswith("openai.types.")
    first, completed = observer.exporter.events
    assert first.event_id != completed.event_id
    assert first.logical_request_id == completed.logical_request_id
    assert first.request_attempt_id != completed.request_attempt_id
    assert (first.retry_number, completed.retry_number) == (0, 1)
    assert first.status == "error" and first.usage_source == "unavailable"
    assert completed.status == "success" and completed.cache_write_tokens == writes
    assert completed.cache_read_tokens == 200 and completed.reasoning_tokens == 20
    price = Catalog().price(completed)
    if writes:
        assert price.amount is None and price.reason == "cache_write_rate_unavailable"
    else:
        assert price.amount is not None
    assert "PRIVATE" not in "".join(event.model_dump_json() for event in observer.exporter.events)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("surface", ["responses", "chat"])
def test_native_httpx2_partial_stream_close_preserves_unknown_usage(
    observer, asynchronous, surface
):
    chunk = (
        {
            "type": "response.output_text.delta",
            "sequence_number": 0,
            "item_id": "msg_owned",
            "output_index": 0,
            "content_index": 0,
            "delta": "PRIVATE",
            "logprobs": [],
        }
        if surface == "responses"
        else {
            **SYNTHETIC_CHAT,
            "object": "chat.completion.chunk",
            "usage": None,
            "choices": [{"index": 0, "delta": {"content": "PRIVATE"}, "finish_reason": None}],
        }
    )
    wire = "data: " + json.dumps(chunk) + "\n\n"

    def handle(request):
        return httpx2.Response(200, text=wire, headers={"content-type": "text/event-stream"})

    kwargs = {"input": "PRIVATE"} if surface == "responses" else {"messages": []}

    async def run():
        async with openai.AsyncOpenAI(
            api_key="synthetic-key",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            stream = await resource.create(model="gpt-4.1-mini", stream=True, **kwargs)
            assert isinstance(stream, openai.AsyncStream)
            async with stream:
                await anext(stream)

    if asynchronous:
        asyncio.run(run())
    else:
        with openai.OpenAI(
            api_key="synthetic-key",
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            stream = resource.create(model="gpt-4.1-mini", stream=True, **kwargs)
            assert isinstance(stream, openai.Stream)
            with stream:
                next(stream)
    (event,) = observer.exporter.events
    assert event.status == "incomplete" and event.usage_source == "unavailable"
    assert event.input_tokens is None and event.output_tokens is None
    assert Catalog().price(event).amount is None and "PRIVATE" not in event.model_dump_json()


def test_native_httpx2_async_cancellation_is_preserved_without_retry(observer):
    attempts = []

    async def run():
        started = asyncio.Event()

        async def handle(request):
            attempts.append(request)
            started.set()
            await asyncio.Event().wait()

        async with openai.AsyncOpenAI(
            api_key="synthetic-key",
            max_retries=2,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle)),
        ) as client:
            task = asyncio.create_task(
                client.responses.create(model="gpt-4.1-mini", input="PRIVATE")
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert len(attempts) == 1
    (event,) = observer.exporter.events
    assert event.status == "error" and event.usage_source == "unavailable"
    assert "PRIVATE" not in event.model_dump_json()
