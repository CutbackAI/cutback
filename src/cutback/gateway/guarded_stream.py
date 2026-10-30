"""Quarantine complete native SSE bytes before delivery. No early payload frames."""

import asyncio
import time

import anyio
from fastapi.responses import Response

from cutback.gateway.streaming import MAX_BYTES, TERMINALS, Frames
from cutback.server.gateway_metering import response_event
from cutback.server.gateway_service import GatewayError


def output_guard_content(body, *, events=None, wire=None, deltas=()):
    """Inspect both complete JSON and text a native consumer can concatenate."""
    parts = []
    if isinstance(body, dict) and isinstance(body.get("output"), list):
        for item in body["output"]:
            if not isinstance(item, dict):
                continue
            for field in ("content", "summary"):
                values = item.get(field)
                if not isinstance(values, list):
                    continue
                for value in values:
                    if isinstance(value, dict):
                        for key in ("text", "refusal"):
                            if isinstance(value.get(key), str):
                                parts.append(value[key])
    return {
        "native_response": body,
        "native_stream_events": events or [],
        "native_wire": wire,
        "joined_text_deltas": "".join(deltas),
        "joined_native_text": "".join(parts),
    }


async def guarded_stream(
    client,
    provider_key,
    raw,
    sessions,
    reservation,
    before,
    output_cap,
    parse,
    checker,
    input_guard,
    settle,
):
    model = reservation.model
    assert model is not None
    upstream = None
    started = time.perf_counter()
    frames = Frames(parse)
    wire, events, deltas = bytearray(), [], []
    terminal_response = None
    try:
        async with asyncio.timeout(model.timeout_seconds):
            request = client.build_request(
                "POST",
                "https://api.openai.com/v1/responses",
                content=raw,
                headers={
                    "authorization": "Bearer " + provider_key.get_secret_value(),
                    "content-type": "application/json",
                    "accept": "text/event-stream",
                    "accept-encoding": "identity",
                },
                timeout=model.timeout_seconds,
            )
            upstream = await client.send(request, stream=True)
            if upstream.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise GatewayError("GATEWAY_UPSTREAM_ENCODING_UNQUALIFIED", 502)
            if (
                upstream.status_code == 200
                and upstream.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream"
            ):
                raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
            async for chunk in upstream.aiter_bytes():
                if len(wire) + len(chunk) > MAX_BYTES:
                    raise GatewayError("GATEWAY_RESPONSE_TOO_LARGE_BILLING_UNCERTAIN", 502)
                wire.extend(chunk)
                if upstream.status_code != 200:
                    continue
                for _, event in frames.feed(chunk):
                    if event is None:
                        continue
                    events.append(event)
                    if event["type"].endswith(".delta") and "delta" in event:
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise GatewayError("GATEWAY_STREAM_PROTOCOL_UNQUALIFIED", 502)
                        deltas.append(delta)
                    if event["type"] in TERMINALS:
                        terminal_response = event.get("response")
                        if (
                            not isinstance(terminal_response, dict)
                            or terminal_response.get("status") != event["type"].split(".")[1]
                        ):
                            raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
            if upstream.status_code == 200:
                frames.end()
            else:
                try:
                    terminal_response = parse(bytes(wire))
                except (ValueError, TypeError, RecursionError):
                    raise GatewayError(
                        "GATEWAY_GUARDED_OUTPUT_MUST_BE_QUALIFIED_JSON", 502
                    ) from None
            event, amount, code = response_event(
                before,
                model,
                terminal_response,
                (time.perf_counter() - started) * 1000,
                upstream.status_code,
                output_cap=output_cap,
            )
            # Usage is durably settled even if the guard subsequently denies
            # delivery. No buffered first delta is reported as delivered TTFT.
            await settle(amount, code, event)
            decision = await checker.check(
                operation_id=reservation.operation_id,
                stage="output",
                content=output_guard_content(
                    terminal_response, events=events, wire=wire.decode("utf-8"), deltas=deltas
                ),
            )
            if decision.action != "allow":
                raise GatewayError("GATEWAY_OUTPUT_BLOCKED_BY_RUNTIME_GUARD", 403)
            return Response(
                bytes(wire),
                status_code=upstream.status_code,
                headers={
                    "content-type": upstream.headers.get("content-type", "application/json"),
                    "x-cutback-output-buffering": "verified-before-delivery",
                    "x-cutback-guardrail-coverage": "complete"
                    if input_guard is not None
                    and input_guard.coverage_complete
                    and decision.coverage_complete
                    else "incomplete",
                },
            )
    finally:
        if upstream is not None:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
