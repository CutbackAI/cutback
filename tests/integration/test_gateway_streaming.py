"""Actual pinned sync/async SDK SSE over loopback, synthetic upstream, real PostgreSQL."""

import asyncio
import json
import time
from uuid import UUID, uuid4

import httpx
import pytest
from end_to_end.test_pipeline import server
from pydantic import SecretStr
from sqlalchemy import func, select
from support_gateway import issue_gateway_key, native_response, prepare_gateway

from cutback.gateway.app import create_gateway
from cutback.gateway.client import operation_headers
from cutback.instrumentation.openai import OpenAIInstrumentation
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow

pytestmark = pytest.mark.postgres


def wire(kind, **values):
    return (
        "event: "
        + kind
        + "\ndata: "
        + json.dumps({"type": kind, **values}, ensure_ascii=False)
        + "\n\n"
    ).encode()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "unicode",
        "missing_usage",
        "interrupted",
        "rate_limit",
        "disconnect",
        "legacy",
        "malformed",
        "truncated",
        "output_cap",
        "lost_settlement_ack",
        "disconnect_after_terminal",
        "no_text",
        "refusal",
        "incomplete",
        "failed",
    ],
)
def test_native_stream_usage_delegation_disconnect_and_no_replay(
    api, tenant, monkeypatch, asynchronous, scenario
):
    from openai import APIError, APIStatusError, AsyncOpenAI, OpenAI
    from openai.types.responses import ResponseTextDeltaEvent

    context = prepare_gateway(api, tenant, streaming=scenario != "legacy")
    key = issue_gateway_key(context)
    calls, closed, emitted = [], [], []
    known = scenario in {
        "success",
        "unicode",
        "lost_settlement_ack",
        "disconnect_after_terminal",
        "no_text",
        "refusal",
    }
    delta = "alpha\u0085beta\u2028gamma\u2029delta" if scenario == "unicode" else "A"
    if scenario == "lost_settlement_ack":
        from cutback.gateway import streaming

        original = streaming.finish
        acknowledgements = []

        def lost_ack(*args, **kwargs):
            result = original(*args, **kwargs)
            if not acknowledgements:
                acknowledgements.append(1)
                raise RuntimeError("owned lost database acknowledgement")
            return result

        monkeypatch.setattr(streaming, "finish", lost_ack)

    class Source(httpx.AsyncByteStream):
        async def __aiter__(self):
            if scenario != "no_text":
                yield wire(
                    "response.refusal.delta"
                    if scenario == "refusal"
                    else "response.output_text.delta",
                    delta=delta,
                    item_id="msg_owned",
                    content_index=0,
                    output_index=0,
                    sequence_number=1,
                    logprobs=[],
                    obfuscation="owned-padding",
                )
            if scenario == "disconnect":
                await asyncio.sleep(2)
            if scenario == "interrupted":
                raise httpx.ReadError("synthetic upstream interrupt")
            if scenario == "malformed":
                yield b'data: {"type":"response.completed","type":"error"}\n\n'
                return
            if scenario == "truncated":
                return
            body = native_response()
            if scenario == "unicode":
                body["output"][0]["content"][0]["text"] = delta
            if scenario == "missing_usage":
                body.pop("usage")
            if scenario == "output_cap":
                body["usage"]["output_tokens"] = 129
                body["usage"]["total_tokens"] = 139
            if scenario == "no_text":
                body["output"] = []
                body["usage"]["output_tokens"] = 0
                body["usage"]["total_tokens"] = 10
            if scenario == "refusal":
                body["output"][0]["content"] = [{"type": "refusal", "refusal": "owned refusal"}]
            if scenario in {"incomplete", "failed"}:
                body["status"] = scenario
            yield wire("response." + body["status"], response=body, sequence_number=2)
            if scenario == "disconnect_after_terminal":
                await asyncio.sleep(2)
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.append(1)

    async def provider(request):
        calls.append(1)
        assert request.headers["accept"] == "text/event-stream"
        assert request.headers["authorization"] == "Bearer owned-synthetic-provider-key-only"
        assert "x-cutback-operation-id" not in request.headers
        if scenario == "rate_limit":
            return httpx.Response(429, json={"error": {"message": "synthetic limit"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Source())

    gateway = create_gateway(
        database_url=context["app"].state.engine.url.render_as_string(hide_password=False),
        enabled=True,
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
        allowed_project_ids=frozenset({context["project"]}),
    )
    extra = operation_headers(uuid4())
    kwargs = dict(
        model="gpt-6-luna",
        input="owned synthetic private phrase",
        max_output_tokens=128,
        store=False,
        service_tier="default",
        stream=True,
        reasoning={"effort": "medium"},
        stream_options={"include_obfuscation": False},
        extra_headers=extra,
    )
    observer = OpenAIInstrumentation(
        service_name="owned-application",
        environment="staging",
        endpoint="http://127.0.0.1:1/v1/ingest/events",
        project_id=str(context["project"]),
        token="synthetic-ingestion-token-" + "x" * 32,
        identity=None,
    )
    monkeypatch.setattr(observer.exporter, "emit", emitted.append)
    with server(gateway) as endpoint:

        async def exercise():
            Native = AsyncOpenAI if asynchronous else OpenAI
            native = Native(api_key=key["token"], base_url=endpoint + "/v1", timeout=5)
            try:
                if scenario in {"rate_limit", "legacy"}:
                    with pytest.raises(APIStatusError) as failure:
                        result = native.responses.create(**kwargs)
                        if asynchronous:
                            await result
                    assert failure.value.status_code == (429 if scenario == "rate_limit" else 422)
                else:
                    result = native.responses.create(**kwargs)
                    stream = await result if asynchronous else result
                    events = []
                    try:
                        if scenario in {"disconnect", "disconnect_after_terminal"}:
                            first = await stream.__anext__() if asynchronous else next(stream)
                            assert isinstance(first, ResponseTextDeltaEvent) and first.delta == "A"
                            if scenario == "disconnect_after_terminal":
                                final = await stream.__anext__() if asynchronous else next(stream)
                                assert final.type == "response.completed"
                        elif scenario in {
                            "interrupted",
                            "malformed",
                            "truncated",
                            "lost_settlement_ack",
                        }:
                            with pytest.raises(APIError):
                                if asynchronous:
                                    async for event in stream:
                                        events.append(event)
                                else:
                                    events.extend(stream)
                        elif asynchronous:
                            async for event in stream:
                                events.append(event)
                        else:
                            events.extend(stream)
                        if scenario in {"success", "missing_usage", "unicode"}:
                            assert isinstance(events[0], ResponseTextDeltaEvent)
                            assert (
                                events[0].delta == delta and events[-1].type == "response.completed"
                            )
                            assert events[0].model_extra["obfuscation"] == "owned-padding"
                        if scenario in {"no_text", "refusal", "incomplete", "failed"}:
                            assert events[-1].type == "response." + (
                                scenario if scenario in {"incomplete", "failed"} else "completed"
                            )
                    finally:
                        result = stream.close()
                        if asynchronous:
                            await result
                if scenario != "legacy":
                    with pytest.raises(APIStatusError) as conflict:
                        result = native.responses.create(**kwargs)
                        if asynchronous:
                            await result
                    assert conflict.value.status_code in (
                        {403, 409} if scenario in {"output_cap", "malformed"} else {409}
                    )
            finally:
                result = native.close()
                if asynchronous:
                    await result

        try:
            asyncio.run(exercise())
        finally:
            observer.close()
        # Disconnect cleanup is asynchronous relative to the customer's close().
        deadline = time.monotonic() + 5
        while True:
            with context["app"].state.sessions() as session:
                row = session.get(
                    GatewayCallRow, (context["project"], UUID(extra["X-Cutback-Operation-ID"]))
                )
                if scenario == "legacy":
                    assert row is None
                    break
                if row.state != "DISPATCHED":
                    assert row.state == ("SETTLED" if known else "UNCERTAIN")
                    assert (row.cost_usd is not None) == known
                    assert row.streaming is True
                    if scenario in {"rate_limit", "no_text", "refusal"}:
                        assert row.first_text_delta_us is None
                    else:
                        assert 0 <= row.first_text_delta_us < 120000000
                    metadata = session.scalar(
                        select(StoredEvent).where(StoredEvent.project_id == context["project"])
                    )
                    assert metadata.event["duration_ms"] is not None
                    if scenario in {"incomplete", "failed"}:
                        assert metadata.event["input_tokens"] == 10
                        assert metadata.event["output_tokens"] == 2
                        assert metadata.event["status"] == (
                            "error" if scenario == "failed" else "incomplete"
                        )
                        assert row.code == "GATEWAY_USAGE_UNPRICED"
                    assert "owned synthetic private phrase" not in json.dumps(metadata.event)
                    assert "owned-padding" not in json.dumps(metadata.event)
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(StoredEvent)
                            .where(StoredEvent.project_id == context["project"])
                        )
                        == 1
                    )
                    budgets = session.scalars(
                        select(GatewayBudgetRow).where(
                            GatewayBudgetRow.project_id == context["project"]
                        )
                    ).all()
                    assert all(b.active_requests == 0 for b in budgets)
                    assert all((b.reserved_usd == 0) == known for b in budgets)
                    break
            assert time.monotonic() < deadline, (
                "stream did not durably settle or retain uncertainty"
            )
            time.sleep(0.02)
    assert calls == ([] if scenario == "legacy" else [1])
    assert emitted == []
    if scenario not in {"legacy", "rate_limit"}:
        assert closed
