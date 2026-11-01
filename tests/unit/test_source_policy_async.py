"""Real pinned AsyncOpenAI types, retries, cancellation and privacy over closed HTTP."""

import asyncio
import json
import logging

import httpx
import pytest
from openai import APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import Response
from test_native_cache import native_body
from test_source_improvement_policy import policy

from cutback.source_policy import responses_create_async


def native(handler, *, retries=0, endpoint="https://api.openai.com/v1/"):
    return AsyncOpenAI(
        api_key="owned-synthetic-async-only",
        max_retries=retries,
        base_url=endpoint,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
    )


@pytest.mark.parametrize("routed", [False, True])
def test_async_preserves_native_objects_and_exact_frames_with_concurrent_private_inputs(
    routed, caplog
):
    calls = []

    async def provider(request):
        body = json.loads(request.content)
        calls.append(body)
        response = native_body()
        response["model"] = body["model"]
        return httpx.Response(200, json=response)

    async def exercise():
        async with native(provider) as client:
            selected = policy(routed=routed)
            texts = ["OWNED_PRIVATE_ASYNC_INPUT", "x" * 100]
            results = await asyncio.gather(
                *(responses_create_async(client, text, selected) for text in texts)
            )
            assert all(type(result) is Response for result in results)
            assert sorted(calls, key=lambda value: value["input"]) == sorted(
                (selected.request(text) for text in texts), key=lambda value: value["input"]
            )
            assert [result.model for result in results] == [
                selected.target(text).model for text in texts
            ]
            assert client.max_retries == 0 and not client.is_closed()
            logging.getLogger("openai._base_client").debug("owned-async-outside-context")

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert "OWNED_PRIVATE_ASYNC_INPUT" not in caplog.text
    assert "OWNED_PRIVATE_VARIANT" not in caplog.text
    assert "OWNED_PRIVATE_BASE_INSTRUCTION" not in caplog.text
    assert "owned-async-outside-context" in caplog.text


def test_async_native_retry_policy_survives_outage_without_policy_fallback():
    calls = []

    async def unavailable(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503, json={"error": {"message": "owned synthetic outage"}})

    async def exercise():
        async with native(unavailable, retries=1) as client:
            with pytest.raises(APIStatusError) as error:
                await responses_create_async(client, "owned short", policy(routed=True))
            assert error.value.status_code == 503
            assert client.max_retries == 1 and len(calls) == 2
            assert {body["model"] for body in calls} == {"gpt-4.1-mini"}

    asyncio.run(exercise())


def test_async_timeout_is_native_and_never_replayed_by_the_policy():
    calls = []

    async def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("owned synthetic timeout", request=request)

    async def exercise():
        async with native(timeout) as client:
            with pytest.raises(APITimeoutError):
                await responses_create_async(client, "owned", policy())
            assert len(calls) == 1 and not client.is_closed()

    asyncio.run(exercise())


def test_async_cancellation_propagates_without_fallback_or_closing_borrowed_client(caplog):
    calls = []

    async def exercise():
        dispatched = asyncio.Event()
        stop = asyncio.Event()

        async def pending(request):
            calls.append(request)
            dispatched.set()
            await stop.wait()
            raise AssertionError("Cancelled native request must not resume")

        async with native(pending) as client:
            task = asyncio.create_task(
                responses_create_async(client, "OWNED_PRIVATE_CANCELLED_INPUT", policy())
            )
            await asyncio.wait_for(dispatched.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert len(calls) == 1 and not client.is_closed()
            logging.getLogger("httpx").debug("owned-async-after-cancellation")

    with caplog.at_level(logging.DEBUG):
        asyncio.run(exercise())
    assert "OWNED_PRIVATE_CANCELLED_INPUT" not in caplog.text
    assert "owned-async-after-cancellation" in caplog.text


def test_async_missing_usage_and_incomplete_native_response_stay_unknown():
    async def provider(request):
        return httpx.Response(200, json=native_body(status="incomplete", usage=False))

    async def exercise():
        async with native(provider) as client:
            result = await responses_create_async(client, "owned", policy())
            assert (
                type(result) is Response and result.usage is None and result.status == "incomplete"
            )

    asyncio.run(exercise())


def test_async_unqualified_inputs_cache_stream_and_endpoint_refuse_before_io():
    calls = []

    async def provider(request):
        calls.append(request)
        raise AssertionError("Unqualified async request dispatched")

    async def exercise():
        async with native(provider) as client:
            for text in ("é" * 65, "x" * 129, "\ud800", None):
                with pytest.raises(ValueError):
                    await responses_create_async(client, text, policy())
            with pytest.raises(ValueError, match="ASYNC_CACHE_RUNTIME_UNQUALIFIED"):
                await responses_create_async(client, "owned", policy(cached=True))
            with pytest.raises(TypeError):
                await responses_create_async(client, "owned", policy(), stream=True)
            with pytest.raises(ValueError, match="ASYNCHRONOUS_CLIENT_REQUIRED"):
                await responses_create_async(object(), "owned", policy())
        async with native(provider, endpoint="https://example.invalid/v1/") as client:
            with pytest.raises(ValueError, match="OFFICIAL_NATIVE_ENDPOINT_REQUIRED"):
                await responses_create_async(client, "owned", policy())
        assert calls == []

    asyncio.run(exercise())
