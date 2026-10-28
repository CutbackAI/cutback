"""Actual pinned native clients and transports, with intercepted synthetic HTTP only."""

import asyncio
import copy
import importlib
import json
from decimal import Decimal
from importlib.metadata import version

import openai
import pytest
from test_instrumentation import SYNTHETIC_CHAT, SYNTHETIC_RESPONSE
from test_instrumentation import observer as observer

from cutback.pricing import Catalog

pytestmark = pytest.mark.skipif(
    version("openai") not in {"2.29.0", "3.35.1"}, reason="qualified modern usage SDK matrix"
)


def payload(surface):
    value = copy.deepcopy(SYNTHETIC_RESPONSE if surface == "responses" else SYNTHETIC_CHAT)
    value["model"] = "gpt-6-luna"
    value["service_tier"] = "default"
    details = value["usage"][
        "input_tokens_details" if surface == "responses" else "prompt_tokens_details"
    ]
    details.update(
        cache_write_tokens=700,
        cache_write_tokens_details={
            "ttl_30m_tokens": 500,
            "ttl_12h_tokens": 200,
        },
    )
    return value


def native_http():
    return importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")


@pytest.mark.parametrize("surface", ["responses", "chat"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_native_lifetime_usage_preserves_retry_stream_response_and_privacy(
    observer, surface, asynchronous, streaming
):
    wire, http = payload(surface), native_http()
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return http.Response(
                503,
                headers={"retry-after-ms": "1"},
                json={"error": {"message": "SYNTHETIC_PRIVATE_ERROR"}},
            )
        if not streaming:
            return http.Response(200, json=wire)
        chunk = (
            {"type": "response.completed", "sequence_number": 0, "response": wire}
            if surface == "responses"
            else {**wire, "object": "chat.completion.chunk"}
        )
        data = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
        return http.Response(200, text=data, headers={"content-type": "text/event-stream"})

    options = (
        {"input": "SYNTHETIC_PRIVATE_INPUT", "reasoning": {"effort": "none"}}
        if surface == "responses"
        else {
            "messages": [{"role": "user", "content": "SYNTHETIC_PRIVATE_INPUT"}],
            "reasoning_effort": "none",
        }
    )
    options.update(model="gpt-6-luna", stream=streaming, service_tier="auto")

    async def asynchronous_call():
        async with openai.AsyncOpenAI(
            api_key="synthetic-no-vendor-key",
            max_retries=1,
            http_client=http.AsyncClient(transport=http.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            result = await resource.create(**options)
            if streaming:
                assert isinstance(result, openai.AsyncStream)
                async with result:
                    chunks = [chunk async for chunk in result]
                assert len(chunks) == 1
            else:
                assert result.id == wire["id"]
                assert result.__class__.__module__.startswith("openai.types.")

    if asynchronous:
        asyncio.run(asynchronous_call())
    else:
        with openai.OpenAI(
            api_key="synthetic-no-vendor-key",
            max_retries=1,
            http_client=http.Client(transport=http.MockTransport(handle)),
        ) as client:
            resource = client.responses if surface == "responses" else client.chat.completions
            result = resource.create(**options)
            if streaming:
                assert isinstance(result, openai.Stream)
                with result:
                    assert len(list(result)) == 1
            else:
                assert result.id == wire["id"]
                assert result.__class__.__module__.startswith("openai.types.")
    assert len(requests) == 2 and requests[0] == requests[1]
    failed, completed = observer.exporter.events
    assert failed.logical_request_id == completed.logical_request_id
    assert failed.request_attempt_id != completed.request_attempt_id
    assert failed.status == "error" and failed.usage_source == "unavailable"
    assert completed.event_schema_version == "1.1" and completed.service_tier == "default"
    assert (completed.cache_write_30m_tokens, completed.cache_write_12h_tokens) == (500, 200)
    assert Catalog().price(completed).amount == Decimal("0.0001745")
    assert "SYNTHETIC_PRIVATE" not in "".join(
        event.model_dump_json() for event in observer.exporter.events
    )


@pytest.mark.parametrize("damage", ["missing_lifetime", "invalid_partition", "bool_count"])
def test_native_bad_lifetime_retains_call_and_response_with_unknown_usage(observer, damage):
    wire, http = payload("responses"), native_http()
    details = wire["usage"]["input_tokens_details"]["cache_write_tokens_details"]
    if damage == "missing_lifetime":
        details.pop("ttl_12h_tokens")
    elif damage == "invalid_partition":
        details["ttl_12h_tokens"] = 201
    else:
        details["ttl_12h_tokens"] = True
    with openai.OpenAI(
        api_key="synthetic-no-vendor-key",
        max_retries=0,
        http_client=http.Client(
            transport=http.MockTransport(lambda _: http.Response(200, json=wire))
        ),
    ) as client:
        result = client.responses.create(model="gpt-6-luna", input="synthetic")
    assert result.id == wire["id"]
    (event,) = observer.exporter.events
    assert event.status == "success" and event.usage_source == "unavailable"
    assert Catalog().price(event).amount is None


def test_served_fast_tier_is_not_mispriced_as_requested_auto(observer):
    wire, http = payload("responses"), native_http()
    wire["service_tier"] = "fast"
    with openai.OpenAI(
        api_key="synthetic-no-vendor-key",
        max_retries=0,
        http_client=http.Client(
            transport=http.MockTransport(lambda _: http.Response(200, json=wire))
        ),
    ) as client:
        client.responses.create(model="gpt-6-luna", input="synthetic", service_tier="auto")
    (event,) = observer.exporter.events
    assert event.service_tier == "fast"
    assert Catalog().price(event).reason == "unsupported_region_deployment_or_tier"


@pytest.mark.parametrize("ttl", ["30m", "12h"])
def test_native_extra_body_lifetime_is_preserved_with_qualified_text_metering(observer, ttl):
    wire, http = payload("responses"), native_http()
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return http.Response(200, json=wire)

    with openai.OpenAI(
        api_key="synthetic-no-vendor-key",
        max_retries=0,
        http_client=http.Client(transport=http.MockTransport(respond)),
    ) as client:
        client.responses.create(
            model="gpt-6-luna",
            input="synthetic",
            reasoning={"effort": "none"},
            extra_body={"prompt_cache_options": {"ttl": ttl}},
        )
    assert bodies[0]["prompt_cache_options"] == {"ttl": ttl}
    (event,) = observer.exporter.events
    assert event.metering_status == "text_only"
    assert Catalog().price(event).amount == Decimal("0.0001745")


def test_unqualified_extra_body_remains_unpriced(observer):
    wire, http = payload("responses"), native_http()
    with openai.OpenAI(
        api_key="synthetic-no-vendor-key",
        max_retries=0,
        http_client=http.Client(
            transport=http.MockTransport(lambda _: http.Response(200, json=wire))
        ),
    ) as client:
        client.responses.create(
            model="gpt-6-luna",
            input="synthetic",
            extra_body={"prompt_cache_options": {"ttl": "12h"}, "unqualified_option": True},
        )
    (event,) = observer.exporter.events
    assert event.metering_status == "unsupported_units"
    assert Catalog().price(event).amount is None


@pytest.mark.parametrize("surface", ["responses.create", "chat.completions.create"])
@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize("tier", [None, "default", "fast"])
def test_owned_execution_never_prices_an_unsupported_served_tier_as_standard(
    monkeypatch, surface, agent, tier
):
    import httpx
    from support_native_agent import native_executor, response_body
    from test_agent_turns import frame
    from test_prompt_executors import bound_model

    from cutback.evals.executors import OpenAIExecutor
    from cutback.evals.experiments import ModelSpec

    calls = []

    def handler(request):
        calls.append(request)
        wire = response_body(surface)
        if tier is not None:
            wire["service_tier"] = tier
        return httpx.Response(200, json=wire)

    if agent:
        executor = native_executor(surface, monkeypatch, handler)
        model = ModelSpec(key="owned", provider="openai", model="gpt-4.1-mini").with_agent_turn(
            frame(surface)
        )
    else:
        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-owned-never-sent")
        executor = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        model = bound_model("openai", surface)
    try:
        result = executor.execute(model, "synthetic", 2)
        assert result.complete
        assert (result.cost_usd is None) == (tier == "fast")
        assert len(calls) == 1
    finally:
        executor.close()
