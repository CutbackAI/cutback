"""Bounded native SSE forwarding with durable, one-shot metadata accounting."""

import asyncio
import re
import time

import anyio
from fastapi.responses import Response, StreamingResponse

from cutback.evals._logging import owned_async_sdk_request
from cutback.server.gateway_metering import response_event
from cutback.server.gateway_service import GatewayError, finish, record_first_text_delta

MAX_BYTES = 4_194_304
MAX_FRAME = 1_048_576
MAX_EVENTS = 16_384
SEPARATOR = re.compile(rb"\r\n\r\n|\n\n|\r\r")
TERMINALS = {"response.completed", "response.incomplete", "response.failed"}
ERROR_FRAME = (
    b'event: error\ndata: {"type":"error","code":"cutback_stream_incomplete",'
    b'"message":"GATEWAY_STREAM_INCOMPLETE_BILLING_MAY_BE_UNCERTAIN",'
    b'"param":null,"error":{"code":"cutback_stream_incomplete",'
    b'"message":"GATEWAY_STREAM_INCOMPLETE_BILLING_MAY_BE_UNCERTAIN"}}\n\n'
)


class Frames:
    """Incremental byte parser; never persist or log native content/deltas."""

    def __init__(self, parse):
        self.parse = parse
        self.buffer = bytearray()
        self.total = self.count = 0
        self.terminal = False
        self.done = False

    def feed(self, chunk):
        self.total += len(chunk)
        if self.total > MAX_BYTES:
            raise GatewayError("GATEWAY_RESPONSE_TOO_LARGE_BILLING_UNCERTAIN", 502)
        self.buffer.extend(chunk)
        result = []
        while match := SEPARATOR.search(self.buffer):
            end = match.end()
            if end > MAX_FRAME:
                raise GatewayError("GATEWAY_STREAM_FRAME_TOO_LARGE", 502)
            frame = bytes(self.buffer[:end])
            del self.buffer[:end]
            data, label = [], None
            for line in re.split(r"\r\n|\r|\n", frame.decode("utf-8")):
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "data":
                    data.append(value)
                elif field == "event":
                    label = value
            event = None
            if data:
                self.count += 1
                if self.count > MAX_EVENTS or self.done:
                    raise GatewayError("GATEWAY_STREAM_PROTOCOL_UNQUALIFIED", 502)
                raw = "\n".join(data)
                if raw == "[DONE]":
                    if not self.terminal:
                        raise GatewayError("GATEWAY_STREAM_INCOMPLETE_BILLING_UNCERTAIN", 502)
                    self.done = True
                else:
                    if self.terminal:
                        raise GatewayError("GATEWAY_STREAM_PROTOCOL_UNQUALIFIED", 502)
                    try:
                        event = self.parse(raw)
                    except (ValueError, TypeError, RecursionError):
                        raise GatewayError("GATEWAY_STREAM_PROTOCOL_UNQUALIFIED", 502) from None
                    if (
                        not isinstance(event, dict)
                        or not isinstance(event.get("type"), str)
                        or label
                        and label != event["type"]
                    ):
                        raise GatewayError("GATEWAY_STREAM_PROTOCOL_UNQUALIFIED", 502)
                    kind = event["type"]
                    if kind in {"response.output_item.added", "response.output_item.done"}:
                        item = event.get("item")
                        if not isinstance(item, dict) or item.get("type") not in {
                            "message",
                            "reasoning",
                        }:
                            raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
                    if kind in {"response.content_part.added", "response.content_part.done"}:
                        part = event.get("part")
                        if not isinstance(part, dict) or part.get("type") not in {
                            "output_text",
                            "refusal",
                        }:
                            raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
                    self.terminal = kind in TERMINALS
            result.append((frame, event))
        if len(self.buffer) > MAX_FRAME:
            raise GatewayError("GATEWAY_STREAM_FRAME_TOO_LARGE", 502)
        return result

    def end(self):
        if self.buffer.strip() or not self.terminal:
            raise GatewayError("GATEWAY_STREAM_INCOMPLETE_BILLING_UNCERTAIN", 502)


class NativeStream(StreamingResponse):
    def __init__(
        self, upstream, sessions, reservation, before, output_cap, parse, started, deadline
    ):
        self.upstream, self.sessions, self.reservation = upstream, sessions, reservation
        self.before, self.output_cap, self.parse = before, output_cap, parse
        self.started, self.deadline = started, deadline
        self.amount, self.event = None, None
        self.first_delta_recorded = False
        self.code = "GATEWAY_STREAM_INTERRUPTED_BILLING_UNCERTAIN"
        self.iterator = self.chunks()
        super().__init__(
            self.iterator, media_type="text/event-stream", headers={"x-accel-buffering": "no"}
        )

    async def settle(self):
        await asyncio.to_thread(
            finish, self.sessions, self.reservation, self.amount, code=self.code, event=self.event
        )

    async def chunks(self):
        frames = Frames(self.parse)
        try:
            async for chunk in self.upstream.aiter_bytes():
                for wire, event in frames.feed(chunk):
                    if (
                        not self.first_delta_recorded
                        and event
                        and event["type"] == "response.output_text.delta"
                        and isinstance(event.get("delta"), str)
                        and event["delta"]
                    ):
                        await asyncio.to_thread(
                            record_first_text_delta,
                            self.sessions,
                            self.reservation,
                            int((time.perf_counter() - self.started) * 1_000_000),
                        )
                        self.first_delta_recorded = True
                    if event and event["type"] in TERMINALS:
                        body = event.get("response")
                        if (
                            not isinstance(body, dict)
                            or body.get("status") != event["type"].split(".")[1]
                        ):
                            raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
                        self.event, self.amount, self.code = response_event(
                            self.before,
                            self.reservation.model,
                            body,
                            (time.perf_counter() - self.started) * 1000,
                            200,
                            output_cap=self.output_cap,
                        )
                        # Store final usage before releasing its terminal frame.
                        # A lost settlement ACK may retry this receipt, never HTTP.
                        await self.settle()
                    yield wire
            frames.end()
        except Exception as exc:
            if self.event is None:
                self.code = exc.code if isinstance(exc, GatewayError) else self.code
            yield ERROR_FRAME

    async def __call__(self, scope, receive, send):
        await self.execute(scope, receive, send)

    @owned_async_sdk_request
    async def execute(self, scope, receive, send):
        try:
            # Includes upstream reads and downstream backpressure, not only headers.
            async with asyncio.timeout_at(self.deadline):
                await super().__call__(scope, receive, send)
        finally:
            # Starlette's background callback alone is insufficient on disconnect.
            # Shield cleanup from both ASGI cancel scopes and asyncio cancellation.
            async def cleanup():
                with anyio.CancelScope(shield=True):
                    try:
                        await self.upstream.aclose()
                    finally:
                        await self.iterator.aclose()
                        if self.event is None:
                            self.event = self.before.model_copy(
                                update={
                                    "duration_ms": (time.perf_counter() - self.started) * 1000,
                                    "http_status": 200,
                                    "status": "incomplete",
                                }
                            )
                        await self.settle()

            task = asyncio.create_task(cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.shield(task)
                raise


async def open_stream(client, provider_key, raw, sessions, reservation, before, output_cap, parse):
    model = reservation.model
    assert model is not None
    started = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + model.timeout_seconds
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
    upstream = None
    try:
        async with asyncio.timeout_at(deadline):
            upstream = await client.send(request, stream=True)
            if upstream.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise GatewayError("GATEWAY_UPSTREAM_ENCODING_UNQUALIFIED", 502)
            if upstream.status_code == 200:
                if upstream.headers.get("content-type", "").split(";", 1)[0] != "text/event-stream":
                    raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
                return NativeStream(
                    upstream, sessions, reservation, before, output_cap, parse, started, deadline
                )
            # Initial native HTTP failures remain native HTTP failures, with no retry.
            body = bytearray()
            async for chunk in upstream.aiter_bytes():
                if len(body) + len(chunk) > MAX_BYTES:
                    raise GatewayError("GATEWAY_RESPONSE_TOO_LARGE_BILLING_UNCERTAIN", 502)
                body.extend(chunk)
            await asyncio.to_thread(
                finish,
                sessions,
                reservation,
                None,
                code="GATEWAY_UPSTREAM_RESPONSE_BILLING_UNCERTAIN",
                event=before.model_copy(
                    update={
                        "duration_ms": (time.perf_counter() - started) * 1000,
                        "http_status": upstream.status_code,
                        "status": "error",
                    }
                ),
            )
            result = Response(
                bytes(body),
                status_code=upstream.status_code,
                headers={"content-type": upstream.headers.get("content-type", "application/json")},
            )
            await upstream.aclose()
            return result
    except BaseException:
        if upstream is not None:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
        raise
