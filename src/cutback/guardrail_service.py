"""Optional stateless guardrail service; no DB, Docker or provider credentials.

Owned operator configuration supplies policy content. Requests stay in RAM;
bounded replay records contain only keyed identities and decision metadata.
"""

import asyncio
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, JsonValue, model_validator

from cutback.core.guardrails import GuardrailCheckReceipt, GuardrailPolicy, GuardrailStage
from cutback.core.models import Identifier, StrictModel
from cutback.core.tool_policy import canonical
from cutback.guardrails import GuardrailEngine, strict_json

MAX_REQUEST_BYTES = 1_100_000


class GuardrailClientAuthority(StrictModel):
    project_id: UUID
    principal_id: UUID
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_ids: tuple[UUID, ...] = Field(min_length=1, max_length=100)
    expires_at: datetime

    @model_validator(mode="after")
    def unique_finite_scope(self):
        if (
            not self.project_id.int
            or not self.principal_id.int
            or any(not value.int for value in self.policy_ids)
            or len(set(self.policy_ids)) != len(self.policy_ids)
            or self.expires_at.tzinfo is None
        ):
            raise ValueError("GUARDRAIL_CLIENT_NONZERO_UNIQUE_FINITE_SCOPE_REQUIRED")
        return self


class GuardrailCheckInput(StrictModel):
    operation_id: UUID
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment: Identifier
    workflow: Identifier
    stage: GuardrailStage
    content: JsonValue


def create_guardrail_service(
    *,
    service_id: UUID,
    policies,
    clients,
    max_inflight=8,
    max_retained=1000,
    checks_per_minute=600,
    allow_insecure_loopback=False,
):
    """One process with finite CPU admission; shared application quotas stay upstream."""
    if (
        not isinstance(service_id, UUID)
        or not service_id.int
        or type(max_inflight) is not int
        or not 1 <= max_inflight <= 64
        or type(max_retained) is not int
        or not 1 <= max_retained <= 10000
        or type(checks_per_minute) is not int
        or not 1 <= checks_per_minute <= 100000
        or not 1 <= len(policies) <= 100
        or not 1 <= len(clients) <= 1000
        or type(allow_insecure_loopback) is not bool
    ):
        raise ValueError("GUARDRAIL_SERVICE_EXPLICIT_BOUNDED_CONFIGURATION_REQUIRED")
    captured = [GuardrailPolicy.model_validate(value.model_dump(mode="json")) for value in policies]
    identities = [
        GuardrailClientAuthority.model_validate(value.model_dump(mode="json")) for value in clients
    ]
    if len({value.policy_id for value in captured}) != len(captured) or len(
        {value.token_sha256 for value in identities}
    ) != len(identities):
        raise ValueError("GUARDRAIL_SERVICE_UNIQUE_POLICIES_AND_CLIENTS_REQUIRED")
    by_id = {value.policy_id: value for value in captured}
    for client in identities:
        for identifier in client.policy_ids:
            policy = by_id.get(identifier)
            if not policy or (policy.project_id, policy.principal_id) != (
                client.project_id,
                client.principal_id,
            ):
                raise ValueError("GUARDRAIL_SERVICE_CLIENT_POLICY_SCOPE_MISMATCH")
    engines = {value.policy_id: GuardrailEngine(value) for value in captured}
    replay: OrderedDict[tuple, tuple[str, dict]] = OrderedDict()
    inflight: dict[tuple, tuple[str, asyncio.Task]] = {}
    windows: dict[str, tuple[int, int]] = {}
    revoked: set[str] = set()
    revoked_policies: set[UUID] = set()
    binding_key = secrets.token_bytes(32)
    available = asyncio.Semaphore(max_inflight)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_):
        try:
            yield
        finally:
            pending = [task for _, task in inflight.values()]
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            replay.clear()
            for engine in engines.values():
                engine.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Host/operator control only; no credential-bearing administrative HTTP route.
    app.state.revoked_client_hashes = revoked
    app.state.revoked_policy_ids = revoked_policies

    def failure(code, status):
        return JSONResponse(
            status_code=status,
            content={"code": code},
            headers={
                "cache-control": "no-store",
                "x-content-type-options": "nosniff",
                "x-should-retry": "false",
                "referrer-policy": "no-referrer",
            },
        )

    def authorize(request, project_id, policy_id):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not 24 <= len(header) <= 256:
            return None
        hashed = hashlib.sha256(header[7:].encode()).hexdigest()
        authority = next(
            (client for client in identities if hmac.compare_digest(client.token_sha256, hashed)),
            None,
        )
        if (
            authority is None
            or hashed in revoked
            or policy_id in revoked_policies
            or authority.project_id != project_id
            or policy_id not in authority.policy_ids
            or datetime.now(UTC) >= authority.expires_at
        ):
            return None
        return authority

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return failure("GUARDRAIL_REQUEST_INVALID", 422)

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.url.scheme != "https" and not (
            allow_insecure_loopback
            and request.url.hostname == "127.0.0.1"
            and request.client
            and request.client.host == "127.0.0.1"
        ):
            return failure("GUARDRAIL_HTTPS_REQUIRED", 400)
        try:
            response = await call_next(request)
        except Exception:
            response = failure("GUARDRAIL_SERVICE_UNAVAILABLE", 503)
        response.headers.update(
            {
                "cache-control": "no-store",
                "x-should-retry": "false",
                "x-content-type-options": "nosniff",
            }
        )
        return response

    @app.get("/v1/projects/{project_id}/guardrails/{policy_id}")
    async def metadata(project_id: UUID, policy_id: UUID, request: Request):
        if authorize(request, project_id, policy_id) is None:
            return failure("GUARDRAIL_SCOPE_DENIED", 403)
        return {"service_id": str(service_id), "policy": engines[policy_id].public_metadata()}

    @app.get("/v1/projects/{project_id}/guardrails/{policy_id}/checks/{operation_id}/{stage}")
    async def lookup(
        project_id: UUID,
        policy_id: UUID,
        operation_id: UUID,
        stage: GuardrailStage,
        request: Request,
    ):
        authority = authorize(request, project_id, policy_id)
        if authority is None:
            return failure("GUARDRAIL_SCOPE_DENIED", 403)
        if datetime.now(UTC) >= by_id[policy_id].expires_at:
            return failure("GUARDRAIL_POLICY_EXPIRED", 403)
        key = (authority.token_sha256, policy_id, operation_id, stage)
        stored = replay.get(key)
        if stored is None:
            return failure("GUARDRAIL_DECISION_NOT_RETAINED_OR_PENDING", 404)
        return stored[1]

    @app.post("/v1/projects/{project_id}/guardrails/{policy_id}/check")
    async def check(project_id: UUID, policy_id: UUID, request: Request):
        authority = authorize(request, project_id, policy_id)
        if authority is None:
            return failure("GUARDRAIL_SCOPE_DENIED", 403)
        if request.url.query or request.headers.get("content-encoding", "identity") != "identity":
            return failure("GUARDRAIL_REQUEST_ENCODING_DENIED", 415)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
            return failure("GUARDRAIL_JSON_REQUIRED", 415)
        length = request.headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
            return failure("GUARDRAIL_REQUEST_BOUND", 413)
        minute = int(time.monotonic() // 60)
        previous, count = windows.get(authority.token_sha256, (minute, 0))
        count = count if previous == minute else 0
        if count >= checks_per_minute:
            return failure("GUARDRAIL_PROCESS_RATE_LIMIT", 429)
        windows[authority.token_sha256] = minute, count + 1
        raw = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > MAX_REQUEST_BYTES:
                        return failure("GUARDRAIL_REQUEST_BOUND", 413)
                    raw.extend(chunk)
            body = GuardrailCheckInput.model_validate(strict_json(bytes(raw)))
        except TimeoutError:
            return failure("GUARDRAIL_REQUEST_DEADLINE", 408)
        except (ValueError, TypeError, RecursionError, UnicodeError):
            return failure("GUARDRAIL_REQUEST_INVALID", 422)
        policy = by_id[policy_id]
        if (
            not body.operation_id.int
            or body.policy_sha256 != policy.policy_sha256
            or body.environment != policy.environment
            or body.workflow not in policy.workflows
            or body.stage not in policy.protected_stages
        ):
            return failure("GUARDRAIL_SCOPE_OR_FROZEN_POLICY_DENIED", 403)
        if datetime.now(UTC) >= policy.expires_at:
            return failure("GUARDRAIL_POLICY_EXPIRED", 403)
        if authorize(request, project_id, policy_id) is None:
            return failure("GUARDRAIL_AUTHORITY_ENDED", 403)
        key = (authority.token_sha256, policy_id, body.operation_id, body.stage)
        signature = hmac.new(
            binding_key, canonical(body.model_dump(mode="json")), hashlib.sha256
        ).hexdigest()
        stored, running = replay.get(key), inflight.get(key)
        if stored is not None:
            if not hmac.compare_digest(signature, stored[0]):
                return failure("GUARDRAIL_OPERATION_CONTENT_CHANGED", 409)
            return stored[1]
        if running is not None:
            if not hmac.compare_digest(signature, running[0]):
                return failure("GUARDRAIL_OPERATION_CONTENT_CHANGED", 409)
            receipt = await asyncio.shield(running[1])
            if (
                authorize(request, project_id, policy_id) is None
                or datetime.now(UTC) >= policy.expires_at
            ):
                return failure("GUARDRAIL_AUTHORITY_ENDED", 403)
            return receipt
        if available.locked():
            return failure("GUARDRAIL_PROCESS_CAPACITY", 503)
        await available.acquire()

        async def compute():
            try:
                decision = await asyncio.to_thread(
                    engines[policy_id].check,
                    operation_id=body.operation_id,
                    stage=body.stage,
                    content=body.content,
                    project_id=project_id,
                    principal_id=authority.principal_id,
                    environment=body.environment,
                    workflow=body.workflow,
                )
                receipt = GuardrailCheckReceipt(
                    service_id=service_id,
                    project_id=project_id,
                    principal_id=authority.principal_id,
                    environment=body.environment,
                    workflow=body.workflow,
                    decision=decision,
                ).model_dump(mode="json")
                replay[key] = signature, receipt
                while len(replay) > max_retained:
                    replay.popitem(last=False)
                return receipt
            finally:
                inflight.pop(key, None)
                available.release()

        task = asyncio.create_task(compute())
        inflight[key] = signature, task
        receipt = await asyncio.shield(task)
        if (
            authorize(request, project_id, policy_id) is None
            or datetime.now(UTC) >= policy.expires_at
        ):
            return failure("GUARDRAIL_AUTHORITY_ENDED", 403)
        return receipt

    class Capacity:
        def __init__(self, application):
            self.application = application
            self.slots = asyncio.Semaphore(max_inflight)

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.application(scope, receive, send)
            if self.slots.locked():
                return await failure("GUARDRAIL_PROCESS_CAPACITY", 503)(scope, receive, send)
            async with self.slots:
                return await self.application(scope, receive, send)

    app.add_middleware(Capacity)
    return app
