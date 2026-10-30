"""Separate scoped tool service, never part of the collector or model-key worker.

Only this separately operated service owns local Docker access. It has no file,
command, URL, SQL-program or plugin execution endpoint. Content stays in memory
and the approved isolated adapter. Default reads expose metadata only.
"""

import asyncio
import hashlib
import hmac
import os
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from cutback.core.models import Identifier, StrictModel
from cutback.core.tool_policy import (
    IsolatedToolPolicy,
    RecordSeed,
    ToolContainmentScope,
    ToolPolicyMetadata,
    canonical,
    content_bound,
)
from cutback.tool_isolation import IsolatedToolSession, local_endpoint, reconcile_owned_containment


class BrokerClient(StrictModel):
    project_id: UUID
    principal_id: UUID
    environments: tuple[Identifier, ...] = Field(default=("staging",), min_length=1, max_length=20)
    workflows: tuple[Identifier, ...] = Field(
        default=("isolated-record-task",), min_length=1, max_length=100
    )
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expires_at: datetime
    enabled: StrictBool = True
    max_active_sessions: int = Field(default=2, strict=True, ge=1, le=4)


class BrokerStart(StrictModel):
    broker_generation: UUID
    policy: IsolatedToolPolicy
    seeds: list[RecordSeed] = Field(min_length=1, max_length=64)
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class BrokerCall(StrictModel):
    operation_id: UUID
    policy_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tool: str = Field(min_length=1, max_length=64)
    arguments: dict = Field(max_length=32)


class BrokerPurpose(StrictModel):
    purpose: Identifier


@dataclass(repr=False)
class ToolOperation:
    request_hmac: str
    state: str = "DISPATCHING"
    result: dict | None = field(default=None, repr=False)


@dataclass(repr=False)
class BrokerSession:
    policy: IsolatedToolPolicy | ToolPolicyMetadata = field(repr=False)
    request_hmac: str
    approval_hash: str
    state: str = "STARTING"
    runtime: IsolatedToolSession | None = field(default=None, repr=False)
    operations: dict[UUID, ToolOperation] = field(default_factory=dict, repr=False)
    lock: Any = field(default_factory=threading.RLock, repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_monotonic: float = field(default_factory=monotonic)
    code: str | None = None

    def receipt(self):
        with self.lock:
            return self._receipt()

    def _receipt(self):
        return {
            "session_id": str(self.policy.session_id),
            "project_id": str(self.policy.project_id),
            "principal_id": str(self.policy.principal_id),
            "environment": self.policy.environment,
            "workflow": self.policy.workflow,
            "state": self.state,
            "code": self.code,
            "policy_sha256": self.policy.policy_sha256,
            "approval_hash": self.approval_hash,
            "image_id": self.policy.image_id,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.policy.expires_at.isoformat(),
            "operations": len(self.operations),
            "completed_operations": sum(op.state == "RETURNED" for op in self.operations.values()),
            "uncertain_operations": sum(op.state == "UNCERTAIN" for op in self.operations.values()),
            "cleanup_confirmed": bool(
                self.runtime and self.runtime.containment_receipt()["cleanup_confirmed"]
            ),
            "tool_cost_usd": None,
            "complete_task_cost_usd": None,
        }


def create_tool_broker(
    *,
    image_id,
    broker_id=None,
    endpoint,
    clients,
    allow_insecure_loopback=False,
    max_active_sessions=4,
    max_retained_sessions=1000,
    runtime_factory=IsolatedToolSession,
):
    """Trusted operator settings are immutable through HTTP. No service is provisioned."""
    if (
        not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
        or not 1 <= max_active_sessions <= 8
        or not 1 <= max_retained_sessions <= 1000
    ):
        raise ValueError("TOOL_BROKER_FIXED_IMAGE_AND_CAPACITY_REQUIRED")
    endpoint = local_endpoint(endpoint)
    broker_id = UUID(str(broker_id)) if broker_id is not None else uuid4()
    if broker_id.int == 0:
        raise ValueError("TOOL_BROKER_STABLE_NONZERO_ID_REQUIRED")
    if not isinstance(clients, (list, tuple)) or not 1 <= len(clients) <= 100:
        raise ValueError("TOOL_BROKER_SCOPED_CLIENTS_REQUIRED")
    registered = [BrokerClient.model_validate_json(client.model_dump_json()) for client in clients]
    if any(client.expires_at.tzinfo is None for client in registered) or len(
        {client.token_sha256 for client in registered}
    ) != len(registered):
        raise ValueError("TOOL_BROKER_UNIQUE_EXPIRING_CLIENTS_REQUIRED")
    generation, fingerprint_key = uuid4(), os.urandom(32)
    sessions: dict[UUID, BrokerSession] = {}
    retired_sessions: dict[UUID, ToolContainmentScope] = {}
    guard, stopping = threading.RLock(), threading.Event()

    def current_authority(client, policy):
        return (
            client.enabled
            and client.expires_at > datetime.now(UTC)
            and client.project_id == policy.project_id
            and client.principal_id == policy.principal_id
            and policy.environment in client.environments
            and policy.workflow in client.workflows
            and datetime.now(UTC) < policy.expires_at
        )

    def terminate(holder):
        with holder.lock:
            holder.state = "CLOSING"
            for operation in holder.operations.values():
                operation.result = None
        try:
            if holder.runtime:
                holder.runtime.close(reconcile=True)
        finally:
            with holder.lock:
                holder.policy = ToolPolicyMetadata.capture(holder.policy)
                confirmed = (
                    holder.runtime and holder.runtime.containment_receipt()["cleanup_confirmed"]
                )
                holder.state = "CLOSED" if confirmed else "UNCERTAIN"
                holder.code = None if confirmed else "TOOL_BROKER_CONTAINMENT_UNCONFIRMED"

    def maintenance():
        while not stopping.wait(1):
            with guard:
                expired = [
                    holder
                    for holder in sessions.values()
                    if holder.state not in {"CLOSED", "CLOSING"}
                    and (
                        datetime.now(UTC) >= holder.policy.expires_at
                        or monotonic() >= holder.started_monotonic + holder.policy.max_wall_seconds
                    )
                ]
            for holder in expired:
                try:
                    terminate(holder)
                except Exception:
                    holder.state, holder.code = "UNCERTAIN", "TOOL_BROKER_CONTAINMENT_UNCONFIRMED"

    @asynccontextmanager
    async def lifespan(app):
        thread = threading.Thread(target=maintenance, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopping.set()
            thread.join(timeout=2)
            for holder in list(sessions.values()):
                try:
                    terminate(holder)
                except Exception:
                    holder.state, holder.code = "UNCERTAIN", "TOOL_BROKER_CONTAINMENT_UNCONFIRMED"

    app = FastAPI(
        title="Cutback isolated tool broker",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.clients, app.state.sessions, app.state.generation = registered, sessions, generation

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.url.scheme != "https" and not (
            allow_insecure_loopback
            and request.client
            and request.client.host in {"127.0.0.1", "::1"}
        ):
            return JSONResponse({"code": "TOOL_BROKER_HTTPS_REQUIRED"}, status_code=400)
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 1048576):
                return JSONResponse({"code": "TOOL_BROKER_BODY_BOUND"}, status_code=413)
            if (
                request.headers.get("content-encoding", "identity") != "identity"
                or request.headers.get("content-type", "").split(";")[0] != "application/json"
            ):
                return JSONResponse({"code": "TOOL_BROKER_JSON_REQUIRED"}, status_code=415)

            async def bounded():
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > 1048576:
                        raise ValueError("BOUND")
                    data.extend(chunk)
                return bytes(data)

            try:
                request._body = await asyncio.wait_for(bounded(), timeout=5)
            except (ValueError, TimeoutError):
                return JSONResponse({"code": "TOOL_BROKER_BODY_BOUND_OR_TIMEOUT"}, status_code=413)
        try:
            response = await call_next(request)
        except Exception:
            return JSONResponse({"code": "TOOL_BROKER_INTERNAL_UNAVAILABLE"}, status_code=503)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return JSONResponse({"code": "TOOL_BROKER_INVALID_SCHEMA"}, status_code=422)

    def authorize(request: Request, *, containment=False):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or not 32 <= len(header[7:]) <= 256:
            raise HTTPException(401, "TOOL_BROKER_SCOPED_KEY_REQUIRED")
        hashed = hashlib.sha256(header[7:].encode()).hexdigest()
        client = next(
            (client for client in registered if hmac.compare_digest(client.token_sha256, hashed)),
            None,
        )
        if (
            not client
            or not containment
            and (not client.enabled or client.expires_at <= datetime.now(UTC))
        ):
            raise HTTPException(401, "TOOL_BROKER_SCOPED_KEY_DENIED")
        return client

    def current(request: Request):
        return authorize(request)

    def contain(request: Request):
        # Retired registered keys retain only their principal's metadata/containment,
        # never result reads, new sessions or further tool execution.
        return authorize(request, containment=True)

    def owned(project, identifier, client):
        with guard:
            holder = sessions.get(identifier)
        if (
            client.project_id != project
            or not holder
            or holder.policy.project_id != project
            or holder.policy.principal_id != client.principal_id
        ):
            raise HTTPException(404, "TOOL_BROKER_SESSION_NOT_FOUND")
        return holder

    def fingerprint(value):
        return hmac.new(fingerprint_key, canonical(value), hashlib.sha256).hexdigest()

    @app.get("/v1/identity")
    def identity(client: Annotated[BrokerClient, Depends(current)]):
        return {
            "broker_generation": str(generation),
            "broker_id": str(broker_id),
            "project_id": str(client.project_id),
            "principal_id": str(client.principal_id),
            "image_id": image_id,
            "profile": "cutback.isolated-record-tools.v1",
        }

    @app.post("/v1/projects/{project_id}/sessions", status_code=202)
    def start(
        project_id: UUID, body: BrokerStart, client: Annotated[BrokerClient, Depends(current)]
    ):
        policy = body.policy
        if (
            body.broker_generation != generation
            or policy.project_id != project_id
            or client.project_id != project_id
            or policy.principal_id != client.principal_id
            or policy.environment not in client.environments
            or policy.workflow not in client.workflows
            or policy.image_id != image_id
            or policy.expires_at <= datetime.now(UTC)
            or policy.expires_at > client.expires_at
        ):
            raise HTTPException(403, "TOOL_BROKER_CURRENT_GENERATION_SCOPE_OR_IMAGE_DENIED")
        content_bound(body.model_dump(mode="json"), 1048576)
        bound = fingerprint(body.model_dump(mode="json"))
        with guard:
            if stopping.is_set():
                raise HTTPException(503, "TOOL_BROKER_STOPPING")
            if policy.session_id in retired_sessions:
                raise HTTPException(409, "TOOL_BROKER_CONTAINED_SESSION_CANNOT_RESTART")
            prior = sessions.get(policy.session_id)
            if prior:
                if prior.request_hmac != bound:
                    raise HTTPException(409, "TOOL_BROKER_SESSION_CONTENT_CONFLICT")
                return prior.receipt()
            active = [holder for holder in sessions.values() if holder.state != "CLOSED"]
            if (
                len(sessions) + len(retired_sessions) >= max_retained_sessions
                or len(active) >= max_active_sessions
                or sum(
                    holder.policy.project_id == project_id
                    and holder.policy.principal_id == client.principal_id
                    for holder in active
                )
                >= client.max_active_sessions
            ):
                raise HTTPException(429, "TOOL_BROKER_SESSION_CAPACITY")
            holder = BrokerSession(policy, bound, body.approval_hash)
            sessions[policy.session_id] = holder

        def prepared(runtime):
            with holder.lock:
                holder.runtime = runtime
                cancelled = (
                    stopping.is_set()
                    or holder.state != "STARTING"
                    or not current_authority(client, policy)
                )
            if cancelled:
                # The driver calls this before spawning. Cancellation must also
                # prevent a delayed constructor from starting a container.
                runtime.close(reconcile=True)

        try:
            runtime = runtime_factory(
                policy, body.seeds, endpoint=endpoint, on_prepared=prepared, broker_id=broker_id
            )
            with holder.lock:
                holder.runtime = runtime
                cancelled = (
                    holder.state != "STARTING"
                    or not current_authority(client, policy)
                    or monotonic() >= holder.started_monotonic + policy.max_wall_seconds
                )
                if not cancelled:
                    holder.state = "READY"
            if cancelled:
                terminate(holder)
        except Exception:
            with holder.lock:
                holder.state, holder.code = (
                    "UNCERTAIN",
                    "TOOL_BROKER_START_FAILED_RECONCILE_NO_RESTART",
                )
        return holder.receipt()

    @app.get("/v1/projects/{project_id}/sessions/{session_id}")
    def show(project_id: UUID, session_id: UUID, client: Annotated[BrokerClient, Depends(contain)]):
        return owned(project_id, session_id, client).receipt()

    @app.post("/v1/projects/{project_id}/sessions/{session_id}/calls")
    def call(
        project_id: UUID,
        session_id: UUID,
        body: BrokerCall,
        client: Annotated[BrokerClient, Depends(current)],
    ):
        holder = owned(project_id, session_id, client)
        content_bound(body.model_dump(mode="json"), 64000)
        bound = fingerprint(body.model_dump(mode="json"))
        with holder.lock:
            if body.policy_sha256 != holder.policy.policy_sha256:
                raise HTTPException(409, "TOOL_BROKER_POLICY_CHANGED")
            if not current_authority(client, holder.policy):
                raise HTTPException(403, "TOOL_BROKER_CURRENT_SESSION_SCOPE_DENIED")
            if monotonic() >= holder.started_monotonic + holder.policy.max_wall_seconds:
                raise HTTPException(409, "TOOL_BROKER_SESSION_NOT_READY")
            prior = holder.operations.get(body.operation_id)
            if prior:
                if prior.request_hmac != bound:
                    raise HTTPException(409, "TOOL_BROKER_OPERATION_CONTENT_CONFLICT")
                if prior.result is not None and holder.state == "READY":
                    return {**prior.result, "replayed": True}
                return {
                    "operation_id": str(body.operation_id),
                    "state": prior.state,
                    "result": None,
                }
            if (
                holder.state != "READY"
                or not holder.runtime
                or datetime.now(UTC) >= holder.policy.expires_at
            ):
                raise HTTPException(409, "TOOL_BROKER_SESSION_NOT_READY")
            if len(holder.operations) >= holder.policy.max_calls:
                raise HTTPException(429, "TOOL_BROKER_OPERATION_LIMIT")
            operation = ToolOperation(bound)
            holder.operations[body.operation_id] = operation
            holder.state = "CALLING"
        try:
            result = holder.runtime.execute(body.operation_id, body.tool, body.arguments)
            with holder.lock:
                operation.state = "RETURNED"
                if (
                    holder.state == "CALLING"
                    and current_authority(client, holder.policy)
                    and monotonic() < holder.started_monotonic + holder.policy.max_wall_seconds
                ):
                    operation.result, holder.state = result, "READY"
                    return result
                return {
                    "operation_id": str(body.operation_id),
                    "state": "FINISHED_AFTER_CANCEL",
                    "result": None,
                }
        except Exception:
            with holder.lock:
                operation.state = "UNCERTAIN"
                if holder.state == "CALLING":
                    holder.state, holder.code = (
                        "UNCERTAIN",
                        "TOOL_BROKER_TOOL_RESPONSE_UNKNOWN_NO_RETRY",
                    )
            return {"operation_id": str(body.operation_id), "state": "UNCERTAIN", "result": None}

    @app.get("/v1/projects/{project_id}/sessions/{session_id}/calls/{operation_id}")
    def operation_status(
        project_id: UUID,
        session_id: UUID,
        operation_id: UUID,
        client: Annotated[BrokerClient, Depends(contain)],
    ):
        holder = owned(project_id, session_id, client)
        with holder.lock:
            operation = holder.operations.get(operation_id)
            if not operation:
                raise HTTPException(404, "TOOL_BROKER_OPERATION_NOT_FOUND")
            return {
                "operation_id": str(operation_id),
                "state": operation.state,
                "result_available": operation.result is not None,
                "tool_cost_usd": None,
            }

    @app.post("/v1/projects/{project_id}/sessions/{session_id}/snapshot")
    def snapshot(
        project_id: UUID,
        session_id: UUID,
        body: BrokerPurpose,
        client: Annotated[BrokerClient, Depends(current)],
    ):
        holder = owned(project_id, session_id, client)
        with holder.lock:
            if (
                holder.state != "READY"
                or not holder.runtime
                or not current_authority(client, holder.policy)
                or monotonic() >= holder.started_monotonic + holder.policy.max_wall_seconds
            ):
                raise HTTPException(409, "TOOL_BROKER_SESSION_NOT_READY")
            holder.state = "SNAPSHOTTING"
        try:
            value = holder.runtime.owner_snapshot()
            with holder.lock:
                if (
                    holder.state != "SNAPSHOTTING"
                    or not current_authority(client, holder.policy)
                    or monotonic() >= holder.started_monotonic + holder.policy.max_wall_seconds
                ):
                    raise HTTPException(409, "TOOL_BROKER_RESULT_AFTER_AUTHORITY_CHANGE_DENIED")
                holder.state = "READY"
                return {
                    "session_id": str(holder.policy.session_id),
                    "policy_sha256": holder.policy.policy_sha256,
                    "state": value,
                    "purpose": body.purpose,
                    "content": "explicit_authorized_evaluator_channel",
                }
        except Exception:
            with holder.lock:
                if holder.state == "SNAPSHOTTING":
                    holder.state, holder.code = "UNCERTAIN", "TOOL_BROKER_SNAPSHOT_UNAVAILABLE"
            raise

    @app.delete("/v1/projects/{project_id}/sessions/{session_id}")
    def close(
        project_id: UUID, session_id: UUID, client: Annotated[BrokerClient, Depends(contain)]
    ):
        holder = owned(project_id, session_id, client)
        terminate(holder)
        return holder.receipt()

    @app.post("/v1/projects/{project_id}/sessions/{session_id}/containment")
    def reconcile(
        project_id: UUID,
        session_id: UUID,
        body: ToolContainmentScope,
        client: Annotated[BrokerClient, Depends(contain)],
    ):
        if (
            body.project_id != project_id
            or body.session_id != session_id
            or body.principal_id != client.principal_id
            or client.project_id != project_id
            or body.broker_id != broker_id
        ):
            raise HTTPException(404, "TOOL_BROKER_SESSION_NOT_FOUND")
        with guard:
            holder = sessions.get(session_id)
            if not holder:
                previous = retired_sessions.get(session_id)
                if previous and previous != body:
                    raise HTTPException(409, "TOOL_BROKER_CONTAINMENT_SCOPE_CONFLICT")
                if len(sessions) + len(retired_sessions) < max_retained_sessions:
                    retired_sessions[session_id] = body
        if holder:
            holder = owned(project_id, session_id, client)
            if body.policy_sha256 != holder.policy.policy_sha256:
                raise HTTPException(409, "TOOL_BROKER_POLICY_CHANGED")
            terminate(holder)
            confirmed = holder.receipt()["cleanup_confirmed"]
        else:
            confirmed = reconcile_owned_containment(endpoint, body)
        return {
            **body.model_dump(mode="json"),
            "cleanup_confirmed": confirmed,
            "state": "CLOSED" if confirmed else "UNCERTAIN",
            "tool_cost_usd": None,
        }

    return app
