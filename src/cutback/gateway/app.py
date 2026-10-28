"""Separate, default-disabled native text gateway; no collector/admin/provider key mixing."""

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import SecretStr, ValidationError

from cutback.evals._logging import owned_async_sdk_request
from cutback.gateway.contracts import ResponsesText
from cutback.server.gateway_service import GatewayError

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 4_194_304


def strict_json(raw):
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("GATEWAY_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("GATEWAY_NONFINITE_JSON")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def create_gateway(
    *,
    database_url=None,
    enabled=False,
    provider_key: SecretStr | None = None,
    hmac_key: SecretStr | None = None,
    transport=None,
    allow_insecure_http=False,
    max_inflight=16,
    allowed_project_ids: frozenset[UUID] | None = None,
):
    if type(max_inflight) is not int or not 1 <= max_inflight <= 100:
        raise ValueError("GATEWAY_PROCESS_CAPACITY_OUTSIDE_BOUNDS")
    engine, sessions, client = None, None, None
    projects = frozenset(allowed_project_ids or ())
    if enabled:
        if (
            not projects
            or len(projects) > 1000
            or any(not isinstance(value, UUID) or value.int == 0 for value in projects)
        ):
            raise ValueError("GATEWAY_EXPLICIT_PROJECT_ALLOWLIST_REQUIRED")
        if (
            not isinstance(provider_key, SecretStr)
            or len(provider_key.get_secret_value()) < 16
            or not isinstance(hmac_key, SecretStr)
            or len(hmac_key.get_secret_value()) < 32
            or not database_url
        ):
            raise ValueError("GATEWAY_EXPLICIT_DATABASE_AND_SECRETS_REQUIRED")
        import httpx

        from cutback.server.db import database

        engine, sessions = database(database_url)
        client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=max_inflight, max_keepalive_connections=max_inflight
            ),
        )

    @asynccontextmanager
    async def lifespan(app):
        yield
        if client is not None:
            await client.aclose()
        if engine is not None:
            engine.dispose()

    app = FastAPI(
        title="Cutback optional application gateway",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.sessions, app.state.engine = sessions, engine

    def error(code, status, request_id):
        return JSONResponse(
            status_code=status,
            headers={
                "x-should-retry": "false",
                "cache-control": "no-store",
                "x-request-id": request_id,
                "x-content-type-options": "nosniff",
                "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
            },
            content={
                "error": {"message": code, "type": "cutback_gateway", "code": code},
                "request_id": request_id,
            },
        )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request_id = str(uuid4())
        if not enabled:
            result = error("GATEWAY_DISABLED", 503, request_id)
        elif (
            not allow_insecure_http
            and request.url.scheme != "https"
            and (not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost"})
        ):
            result = error("GATEWAY_HTTPS_REQUIRED", 400, request_id)
        else:
            try:
                result = await call_next(request)
            except GatewayError as exc:
                result = error(exc.code, exc.status, request_id)
            except Exception:
                result = error("GATEWAY_UNAVAILABLE_NO_AUTOMATIC_RETRY", 503, request_id)
        # Pinned native SDKs honor this even for normally retryable 409/429/5xx.
        # Transport loss still requires a stable operation UUID for deduplication.
        result.headers.update(
            {
                "x-should-retry": "false",
                "x-request-id": request_id,
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
                "referrer-policy": "no-referrer",
                "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
            }
        )
        return result

    @app.post("/v1/responses")
    @owned_async_sdk_request
    async def responses(request: Request):
        from sqlalchemy import select

        from cutback.core.models import LLMEvent
        from cutback.server.db import StoredEvent
        from cutback.server.gateway_metering import response_event
        from cutback.server.gateway_service import active_key, finish, recover_leases, reserve
        from cutback.server.operations import enforce_quota

        if sessions is None or client is None or provider_key is None or hmac_key is None:
            raise GatewayError("GATEWAY_DISABLED", 503)
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 110:
            raise GatewayError("GATEWAY_VIRTUAL_KEY_REQUIRED", 401)
        token = authorization[7:]

        def authenticate():
            from fastapi import HTTPException

            from cutback.server.db import token_hash

            with sessions() as session:
                row = active_key(session, token, datetime.now(UTC))
                project = row.project_id
                if project not in projects:
                    raise GatewayError("GATEWAY_PROJECT_NOT_AUTHORIZED_BY_OPERATOR", 403)
            try:
                enforce_quota(sessions, token_hash("gateway:" + token), 600)
            except HTTPException:
                raise GatewayError("GATEWAY_RATE_LIMITED", 429) from None
            recover_leases(sessions, project_id=project)

        await asyncio.to_thread(authenticate)
        try:
            operation_id = UUID(request.headers.get("x-cutback-operation-id", ""))
            if operation_id.int == 0 or request.headers.get("x-cutback-observation-delegate") != (
                "gateway-native-text-v1"
            ):
                raise ValueError("operation contract")
        except (ValueError, TypeError, AttributeError):
            raise GatewayError("GATEWAY_OPERATION_AND_DELEGATION_HEADERS_REQUIRED", 400) from None
        if request.url.query or request.headers.get("content-encoding", "identity") != "identity":
            raise GatewayError("GATEWAY_REQUEST_ENCODING_UNQUALIFIED", 415)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            raise GatewayError("GATEWAY_JSON_REQUIRED", 415)
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
            raise GatewayError("GATEWAY_REQUEST_TOO_LARGE", 413)
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                        raise GatewayError("GATEWAY_REQUEST_TOO_LARGE", 413)
                    raw.extend(chunk)
            specification = ResponsesText.model_validate(strict_json(bytes(raw)))
        except (ValidationError, ValueError, TypeError, RecursionError):
            raise GatewayError("GATEWAY_TEXT_REQUEST_CONTRACT_UNQUALIFIED", 422) from None
        except TimeoutError:
            raise GatewayError("GATEWAY_REQUEST_BODY_TIMEOUT", 408) from None
        signature = hmac.new(
            hmac_key.get_secret_value().encode(),
            b"cutback-gateway-request-v1:"
            + operation_id.bytes
            + hashlib.sha256(token.encode()).digest()
            + bytes(raw),
            hashlib.sha256,
        ).hexdigest()
        try:
            reservation = await asyncio.to_thread(
                reserve,
                sessions,
                token,
                operation_id,
                signature,
                specification,
                traceparent=request.headers.get("traceparent"),
            )
        except ValueError:
            raise GatewayError("GATEWAY_REQUEST_OUTSIDE_APPROVED_CONTRACT", 422) from None

        def event_before():
            with sessions() as session:
                row = session.scalar(
                    select(StoredEvent).where(
                        StoredEvent.project_id == reservation.project_id,
                        StoredEvent.event_id == reservation.event_id,
                    )
                )
                if row is None:
                    raise GatewayError("GATEWAY_CANONICAL_RECEIPT_UNAVAILABLE", 503)
                return LLMEvent.model_validate(row.event)

        try:
            before = await asyncio.to_thread(event_before)
            started = time.perf_counter()
            raw_response = bytearray()
            assert reservation.model is not None
            async with asyncio.timeout(reservation.model.timeout_seconds):
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/responses",
                    content=bytes(raw),
                    headers={
                        "authorization": "Bearer " + provider_key.get_secret_value(),
                        "content-type": "application/json",
                        "accept": "application/json",
                        "accept-encoding": "identity",
                    },
                    timeout=reservation.model.timeout_seconds,
                ) as upstream:
                    if (
                        upstream.headers.get("content-encoding", "identity").strip().lower()
                        != "identity"
                    ):
                        raise GatewayError("GATEWAY_UPSTREAM_ENCODING_UNQUALIFIED", 502)
                    if (
                        upstream.status_code == 200
                        and upstream.headers.get("content-type", "").split(";", 1)[0]
                        != "application/json"
                    ):
                        raise GatewayError("GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED", 502)
                    async for chunk in upstream.aiter_bytes():
                        if len(raw_response) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise GatewayError("GATEWAY_RESPONSE_TOO_LARGE_BILLING_UNCERTAIN", 502)
                        raw_response.extend(chunk)
                    status = upstream.status_code
                    headers = {
                        "content-type": upstream.headers.get("content-type", "application/json")
                    }
            duration = (time.perf_counter() - started) * 1000
            try:
                body = strict_json(bytes(raw_response))
            except (ValueError, TypeError, RecursionError):
                body = None
            event, amount, code = response_event(
                before,
                reservation.model,
                body,
                duration,
                status,
                output_cap=specification.max_output_tokens,
            )
            await asyncio.to_thread(finish, sessions, reservation, amount, code=code, event=event)
            return Response(content=bytes(raw_response), status_code=status, headers=headers)
        except asyncio.CancelledError:
            await asyncio.shield(
                asyncio.to_thread(
                    finish, sessions, reservation, None, code="GATEWAY_CANCELLED_BILLING_UNCERTAIN"
                )
            )
            raise
        except Exception as exc:
            await asyncio.to_thread(
                finish,
                sessions,
                reservation,
                None,
                code=exc.code
                if isinstance(exc, GatewayError)
                else "GATEWAY_INTERRUPTED_BILLING_UNCERTAIN",
            )
            if isinstance(exc, GatewayError):
                raise
            raise GatewayError("GATEWAY_UPSTREAM_OR_STORAGE_UNCERTAIN_NO_RETRY", 503) from None

    class Capacity:
        def __init__(self, app):
            self.app = app
            self.semaphore = asyncio.Semaphore(max_inflight)

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            if self.semaphore.locked():
                return await error("GATEWAY_CAPACITY_BEFORE_DISPATCH", 503, str(uuid4()))(
                    scope, receive, send
                )
            async with self.semaphore:
                # Covers response transmission too, so slow clients cannot keep
                # unlimited bounded response buffers alive after dispatch ends.
                return await self.app(scope, receive, send)

    app.add_middleware(Capacity)
    return app
