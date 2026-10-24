import asyncio
import os
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field, SecretStr, StringConstraints
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from cutback.core.models import EventBatch, StrictModel
from cutback.server.db import (
    AccessKey,
    AuditEvent,
    Environment,
    Organization,
    Project,
    StoredEvent,
    database,
    new_token,
    token_hash,
    token_matches,
)
from cutback.server.repository import PostgresEventRepository

ShortName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]


class ProjectInput(StrictModel):
    slug: ShortName
    environment: ShortName = "staging"


class KeyInput(StrictModel):
    role: Literal["ingest", "viewer"]
    environment: ShortName = "staging"
    expires_in_days: int = Field(default=30, ge=1, le=365)


def create_app(
    *,
    database_url: str | None = None,
    admin_token: str | None = None,
    allow_insecure_http: bool = False,
    rate_limit: int = 600,
):
    database_url = database_url or os.environ["CUTBACK_DATABASE_URL"]
    admin = SecretStr(admin_token or os.environ["CUTBACK_ADMIN_TOKEN"])
    if len(admin.get_secret_value()) < 32:
        raise ValueError("bootstrap admin token must have at least 32 characters")
    admin_digest = token_hash(admin.get_secret_value())
    engine, sessions = database(database_url)
    repository = PostgresEventRepository()
    metrics: Counter = Counter()
    metric_lock = threading.Lock()
    quota_lock = threading.Lock()
    quotas: dict[str, tuple[int, int]] = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="Cutback Collector and Control API", version="1.0", lifespan=lifespan)
    app.state.engine = engine
    app.state.sessions = sessions

    def increment(key, value=1):
        with metric_lock:
            metrics[key] += value

    def error(code, status, request_id, retryable=False):
        return JSONResponse(
            status_code=status,
            content={
                "code": code,
                "message": code,
                "request_id": request_id,
                "retryable": retryable,
            },
        )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        request.state.request_id = str(uuid4())
        if not allow_insecure_http and request.url.scheme != "https":
            if not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
                return error("HTTPS_REQUIRED", 400, request.state.request_id)
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > 1_048_576):
                return error("BODY_TOO_LARGE", 413, request.state.request_id)
            if request.headers.get("content-encoding", "identity") != "identity":
                return error("CONTENT_ENCODING_UNSUPPORTED", 415, request.state.request_id)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return error("JSON_REQUIRED", 415, request.state.request_id)

            async def read_bounded():
                body = bytearray()
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 1_048_576:
                        raise ValueError("body cap")
                    body.extend(chunk)
                return bytes(body)

            try:
                request._body = await asyncio.wait_for(read_bounded(), timeout=5)
            except ValueError:
                return error("BODY_TOO_LARGE", 413, request.state.request_id)
            except TimeoutError:
                return error("BODY_TIMEOUT", 408, request.state.request_id, True)
        try:
            response = await call_next(request)
        except Exception:
            increment("internal_failures")
            return error("INTERNAL_ERROR", 500, request.state.request_id, True)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        increment("validation_rejected")
        # Pydantic's default errors include user input: never echo them.
        return error("INVALID_SCHEMA_OR_CONTENT_FIELDS", 422, request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(
            str(exc.detail),
            exc.status_code,
            request.state.request_id,
            exc.status_code in {429, 503},
        )

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request, exc):
        increment("storage_failures")
        return error("STORAGE_UNAVAILABLE", 503, request.state.request_id, True)

    def authorize(request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 1024:
            increment("auth_failures")
            raise HTTPException(401, "AUTH_REQUIRED")
        token = header[7:]
        hashed = token_hash(token)
        # Bounded fixed-window per-key quota. Multi-replica deployments need shared gateway limits.
        with quota_lock:
            minute = int(time.monotonic() / 60)
            if len(quotas) >= 10000:
                quotas.clear()
            key = hashed if token_matches(token, admin_digest) else "unverified:" + hashed[:4]
            window, count = quotas.get(key, (minute, 0))
            count = count + 1 if window == minute else 1
            quotas[key] = (minute, count)
            if count > rate_limit:
                increment("rate_limited")
                raise HTTPException(429, "RATE_LIMITED")
        if token_matches(token, admin_digest):
            return {"role": "admin", "actor": "bootstrap_admin"}
        with sessions() as session:
            key_row = session.scalar(select(AccessKey).where(AccessKey.digest == hashed))
            if (
                not key_row
                or key_row.revoked_at
                or (key_row.expires_at and key_row.expires_at <= datetime.now(UTC))
            ):
                increment("auth_failures")
                raise HTTPException(401, "INVALID_OR_EXPIRED_KEY")
            return {
                "role": key_row.role,
                "project_id": key_row.project_id,
                "environment_id": key_row.environment_id,
                "actor": str(key_row.id),
            }

    def scope(auth, project_id, roles=("viewer", "admin")):
        if auth["role"] not in roles or (
            auth["role"] != "admin" and auth["project_id"] != project_id
        ):
            raise HTTPException(403, "SCOPE_DENIED")

    @app.get("/v1/health/live")
    def live():
        return {"status": "live", "schema_version": "1.0"}

    @app.get("/v1/health/ready")
    def ready(auth=Depends(authorize)):
        with sessions() as session:
            session.execute(text("SELECT 1"))
            revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            if revision != "0001":
                raise HTTPException(503, "MIGRATION_REQUIRED")
        return {"status": "ready", "schema_revision": revision}

    @app.post("/v1/projects", status_code=201)
    def project_create(body: ProjectInput, auth=Depends(authorize)):
        scope(auth, None, ("admin",))
        with sessions.begin() as session:
            organization = session.scalar(select(Organization).limit(1))
            if not organization:
                organization = Organization(name="Self-hosted")
                session.add(organization)
                session.flush()
            project = Project(organization_id=organization.id, slug=body.slug)
            session.add(project)
            try:
                session.flush()
            except IntegrityError:
                raise HTTPException(409, "PROJECT_SLUG_EXISTS") from None
            session.add(Environment(project_id=project.id, name=body.environment))
            session.add(
                AuditEvent(
                    project_id=project.id, actor=auth["actor"], action="project_created", details={}
                )
            )
        return {"id": str(project.id), "slug": project.slug, "environment": body.environment}

    @app.get("/v1/projects")
    def projects(
        auth=Depends(authorize),
        limit: int = Query(100, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        if auth["role"] == "ingest":
            raise HTTPException(403, "SCOPE_DENIED")
        query = select(Project).order_by(Project.created_at, Project.id).limit(limit).offset(offset)
        if auth["role"] != "admin":
            query = query.where(Project.id == auth["project_id"])
        with sessions() as session:
            return [{"id": str(row.id), "slug": row.slug} for row in session.scalars(query)]

    @app.post("/v1/projects/{project_id}/keys", status_code=201)
    def key_create(project_id: UUID, body: KeyInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        token = new_token()
        with sessions.begin() as session:
            if not session.get(Project, project_id):
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            if not environment:
                raise HTTPException(422, "ENVIRONMENT_NOT_CONFIGURED")
            key_row = AccessKey(
                project_id=project_id,
                environment_id=environment.id if body.role == "ingest" else None,
                digest=token_hash(token),
                role=body.role,
                expires_at=datetime.now(UTC) + timedelta(days=body.expires_in_days),
            )
            session.add(key_row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="key_created",
                    details={"key_id": str(key_row.id), "role": body.role},
                )
            )
        return {
            "key_id": str(key_row.id),
            "token": token,
            "role": body.role,
            "expires_at": key_row.expires_at.isoformat() if key_row.expires_at else None,
            "display_once": True,
        }

    @app.delete("/v1/projects/{project_id}/keys/{key_id}")
    def key_revoke(project_id: UUID, key_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(AccessKey, key_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "KEY_NOT_FOUND")
            row.revoked_at = datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="key_revoked",
                    details={"key_id": str(key_id)},
                )
            )
        return {"status": "revoked"}

    @app.post("/v1/ingest/events")
    def ingest(body: EventBatch, auth=Depends(authorize)):
        scope(auth, body.project_id, ("ingest",))
        now = datetime.now(UTC)
        with sessions() as session:
            environment = session.get(Environment, auth["environment_id"])
            if not environment or any(
                event.environment != environment.name for event in body.events
            ):
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
            if any(
                not now - timedelta(days=30) <= event.occurred_at <= now + timedelta(minutes=5)
                for event in body.events
            ):
                raise HTTPException(422, "TIMESTAMP_OUT_OF_RANGE")
            receipt = repository.ingest_batch(session, body, {environment.name: environment.id})
        increment("accepted", receipt.accepted)
        increment("duplicates", receipt.duplicate)
        return receipt

    @app.get("/v1/projects/{project_id}/usage")
    def usage(
        project_id: UUID,
        start: datetime | None = None,
        end: datetime | None = None,
        group_by: Literal["model", "provider", "workflow", "environment", "day"] = "model",
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        end = end or datetime.now(UTC)
        start = start or end - timedelta(days=7)
        if (
            not start.tzinfo
            or not end.tzinfo
            or not timedelta(0) < end - start <= timedelta(days=395)
        ):
            raise HTTPException(422, "INVALID_TIME_RANGE")
        with sessions() as session:
            return repository.query_usage(session, project_id, start, end, group_by)

    @app.get("/v1/projects/{project_id}/events")
    def events(
        project_id: UUID,
        auth=Depends(authorize),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.scalars(
                select(StoredEvent)
                .where(StoredEvent.project_id == project_id)
                .order_by(StoredEvent.occurred_at.desc(), StoredEvent.id)
                .limit(limit)
                .offset(offset)
            )
            return [
                {
                    "event": row.event,
                    "cost_usd": str(row.cost_usd) if row.cost_usd is not None else None,
                    "cost_status": row.cost_status,
                    "cost_reason": row.cost_reason,
                    "price_snapshot_id": row.price_snapshot_id,
                }
                for row in rows
            ]

    @app.get("/v1/projects/{project_id}/audit")
    def audit(
        project_id: UUID,
        auth=Depends(authorize),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
    ):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            rows = session.scalars(
                select(AuditEvent)
                .where(AuditEvent.project_id == project_id)
                .order_by(AuditEvent.occurred_at.desc())
                .limit(limit)
                .offset(offset)
            )
            return [
                {
                    "action": row.action,
                    "actor": row.actor,
                    "details": row.details,
                    "occurred_at": row.occurred_at.isoformat(),
                }
                for row in rows
            ]

    @app.get("/metrics", include_in_schema=False)
    def prometheus(auth=Depends(authorize)):
        scope(auth, None, ("admin",))
        with metric_lock:
            output = "\n".join(
                f"cutback_collector_{key}_total {value}" for key, value in sorted(metrics.items())
            )
        return Response(output + "\n", media_type="text/plain")

    return app
