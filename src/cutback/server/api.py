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
from pydantic import UUID4, Field, SecretStr, StringConstraints
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from cutback.core.bootstrap import BOOTSTRAP_CONTRACT, ScopedRole
from cutback.core.models import EventBatch, StrictModel
from cutback.server.db import (
    AccessKey,
    AuditEvent,
    Environment,
    Organization,
    Project,
    ScanRun,
    StoredEvent,
    database,
    new_token,
    token_hash,
    token_matches,
)
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.repository import PostgresEventRepository

ShortName = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]


class ProjectInput(StrictModel):
    slug: ShortName
    environment: ShortName = "staging"
    organization_id: UUID | None = None


class KeyInput(StrictModel):
    role: ScopedRole
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

    app = FastAPI(
        title="Cutback Collector and Control API",
        version="1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.engine = engine
    app.state.sessions = sessions
    from cutback.server.case_evidence import install_case_evidence

    # Same authentication and project scope as every other control route.
    from cutback.server.identity import configured_verifier

    app.state.oidc_verifier = configured_verifier()

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
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        increment("validation_rejected")
        # Pydantic's default errors include user input: never echo them.
        status = 400 if request.url.path == "/v1/ingest/otel/v1/traces" else 422
        return error("INVALID_SCHEMA_OR_CONTENT_FIELDS", status, request.state.request_id)

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
        if not header.startswith("Bearer ") or len(header) > 8200:
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
            from cutback.server.operations import enforce_quota

            enforce_quota(sessions, hashed, rate_limit)
            return {"role": "admin", "actor": "bootstrap_admin"}
        if app.state.oidc_verifier is not None and token.count(".") == 2:
            from cutback.server.operations import enforce_quota

            try:
                identity = app.state.oidc_verifier.principal(token, sessions)
            except Exception:
                increment("auth_failures")
                raise HTTPException(401, "INVALID_OIDC_IDENTITY") from None
            enforce_quota(sessions, token_hash(identity["actor"]), rate_limit)
            return identity
        with sessions() as session:
            key_row = session.scalar(select(AccessKey).where(AccessKey.digest == hashed))
            if (
                not key_row
                or key_row.revoked_at
                or (key_row.expires_at and key_row.expires_at <= datetime.now(UTC))
            ):
                increment("auth_failures")
                raise HTTPException(401, "INVALID_OR_EXPIRED_KEY")
            identity = {
                "role": key_row.role,
                "project_id": key_row.project_id,
                "environment_id": key_row.environment_id,
                "actor": str(key_row.id),
            }
        from cutback.server.operations import enforce_quota

        enforce_quota(sessions, hashed, rate_limit)
        return identity

    def scope(auth, project_id, roles=("viewer", "admin")):
        hierarchy = {
            "viewer": {"viewer", "analyst", "editor", "project_admin", "admin"},
            "analyst": {"analyst", "editor", "project_admin", "admin"},
            "editor": {"editor", "project_admin", "admin"},
            "admin": {"project_admin", "admin"},
            "ingest": {"ingest"},
        }
        permitted = set().union(*(hierarchy.get(role, {role}) for role in roles))
        effective_role = (
            auth.get("project_roles", {}).get(project_id)
            if auth["role"] == "oidc"
            else auth["role"]
        )
        if effective_role not in permitted or (
            auth.get("project_id") is not None and auth["project_id"] != project_id
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
            if revision != CURRENT_REVISION:
                raise HTTPException(503, "MIGRATION_REQUIRED")
        from cutback.core.cache_workload import CACHE_WORKLOAD_CONTRACT
        from cutback.core.exact_cache import EXACT_CACHE_CONTRACT
        from cutback.core.native_fixture import NATIVE_FIXTURE_CONTRACT
        from cutback.core.preparation import PREPARATION_CONTRACT

        return {
            "status": "ready",
            "schema_revision": revision,
            "preparation_contract": PREPARATION_CONTRACT,
            "bootstrap_contract": BOOTSTRAP_CONTRACT,
            "native_fixture_contract": NATIVE_FIXTURE_CONTRACT,
            "exact_cache_contract": EXACT_CACHE_CONTRACT,
            "cache_workload_assessment_contract": CACHE_WORKLOAD_CONTRACT,
        }

    @app.post("/v1/projects", status_code=201)
    def project_create(
        body: ProjectInput, auth=Depends(authorize), *, request_id: UUID4 | None = None
    ):
        if auth["role"] == "oidc":
            if auth["organizations"].get(body.organization_id) not in {"owner", "admin"}:
                raise HTTPException(403, "ORGANIZATION_ADMIN_REQUIRED")
        else:
            scope(auth, None, ("admin",))
        with sessions.begin() as session:
            from cutback.core.request_profiles import fingerprint
            from cutback.server.dataset_lineage import lock_preparation_identity

            request_hash = fingerprint(body.model_dump(mode="json"))
            if request_id:
                lock_preparation_identity(session, "project-bootstrap", request_id)
                from cutback.server.dataset_lineage import lock_lineage

                lock_lineage(session, request_id)
                deleted = session.scalar(
                    select(AuditEvent.id)
                    .where(
                        AuditEvent.project_id.is_(None),
                        AuditEvent.action == "project_deleted",
                        AuditEvent.details["project_id"].as_string() == str(request_id),
                    )
                    .limit(1)
                )
                if deleted:
                    raise HTTPException(409, "PROJECT_BOOTSTRAP_WAS_DELETED")
                existing = session.get(Project, request_id)
                if existing:
                    if auth["role"] == "oidc" and auth["organizations"].get(
                        existing.organization_id
                    ) not in {"owner", "admin"}:
                        raise HTTPException(404, "PROJECT_NOT_FOUND")
                    prior = session.scalar(
                        select(AuditEvent.id)
                        .where(
                            AuditEvent.project_id == existing.id,
                            AuditEvent.action == "project_created",
                            AuditEvent.details["bootstrap_request_sha256"].as_string()
                            == request_hash,
                        )
                        .limit(1)
                    )
                    environment = session.scalar(
                        select(Environment.id).where(
                            Environment.project_id == existing.id,
                            Environment.name == body.environment,
                        )
                    )
                    if (
                        not prior
                        or not environment
                        or existing.slug != body.slug
                        or body.organization_id is not None
                        and existing.organization_id != body.organization_id
                    ):
                        raise HTTPException(409, "PROJECT_BOOTSTRAP_IDENTITY_CONFLICT")
                    return {
                        "id": str(existing.id),
                        "slug": existing.slug,
                        "environment": body.environment,
                    }
            if body.organization_id is None:
                lock_preparation_identity(session, "organization-bootstrap", UUID(int=0))
            organization = (
                session.get(Organization, body.organization_id)
                if body.organization_id
                else session.scalar(select(Organization).limit(1))
            )
            if body.organization_id and not organization:
                raise HTTPException(404, "ORGANIZATION_NOT_FOUND")
            if not organization:
                organization = Organization(name="Self-hosted")
                session.add(organization)
                session.flush()
            project = Project(organization_id=organization.id, slug=body.slug)
            if request_id:
                project.id = request_id
            session.add(project)
            try:
                session.flush()
            except IntegrityError:
                raise HTTPException(409, "PROJECT_SLUG_EXISTS") from None
            session.add(Environment(project_id=project.id, name=body.environment))
            session.add(
                AuditEvent(
                    project_id=project.id,
                    actor=auth["actor"],
                    action="project_created",
                    details={"bootstrap_request_sha256": request_hash} if request_id else {},
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
        if auth["role"] == "oidc":
            query = query.where(Project.id.in_(auth["project_roles"]))
        elif auth["role"] != "admin":
            query = query.where(Project.id == auth["project_id"])
        with sessions() as session:
            return [
                {"id": str(row.id), "slug": row.slug, "organization_id": str(row.organization_id)}
                for row in session.scalars(query)
            ]

    @app.get("/v1/projects/{project_id}")
    def project_metadata(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            project = session.get(Project, project_id)
            if not project:
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            return {
                "id": str(project.id),
                "slug": project.slug,
                "organization_id": str(project.organization_id),
            }

    @app.post("/v1/projects/{project_id}/keys", status_code=201)
    def key_create(project_id: UUID, body: KeyInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        token = new_token()
        with sessions.begin() as session:
            if not session.get(Project, project_id):
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            from cutback.server.bootstrap_api import enforce_key_quota

            enforce_key_quota(session, project_id)
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id, Environment.name == body.environment
                )
            )
            if not environment:
                raise HTTPException(422, "ENVIRONMENT_NOT_CONFIGURED")
            key_row = AccessKey(
                project_id=project_id,
                environment_id=environment.id if body.role in {"ingest", "prompt_reader"} else None,
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

    @app.get("/v1/projects/{project_id}/keys")
    def key_list(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "role": row.role,
                    "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                    "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
                }
                for row in session.scalars(
                    select(AccessKey)
                    .where(AccessKey.project_id == project_id)
                    .order_by(AccessKey.id)
                    .limit(100)
                )
            ]

    @app.post("/v1/ingest/events")
    def ingest(body: EventBatch, auth=Depends(authorize)):
        scope(auth, body.project_id, ("ingest",))
        if any(event.event_schema_version == "1.2" for event in body.events):
            raise HTTPException(422, "GATEWAY_RECEIPTS_REQUIRE_INTERNAL_RESERVATION")
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
        group_by: Literal[
            "model", "provider", "workflow", "environment", "day", "call_site", "traffic"
        ] = "model",
        traffic_kind: Literal[
            "application", "evaluation", "judge", "optimization", "simulation", "unknown"
        ]
        | None = None,
        call_site: Annotated[str | None, Query(max_length=200)] = None,
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
            report = repository.query_usage(
                session,
                project_id,
                start,
                end,
                group_by,
                traffic_kind=traffic_kind,
                call_site=call_site,
            )

            if group_by in {"workflow", "call_site"}:
                from cutback.core.inventory import workflow_identity

                scan = session.scalar(
                    select(ScanRun)
                    .where(ScanRun.project_id == project_id)
                    .order_by(ScanRun.created_at.desc())
                    .limit(1)
                )
                from cutback.server.workflow_labels import workflow_labels

                labels = workflow_labels(session, project_id)
                if scan:
                    for site in scan.inventory["call_sites"]:
                        labels[site["call_site_id"]] = (
                            f"{site['source_file']}:{site['line']} · {site['source_symbol']}"
                        )
                        for entry in site["entry_points"]:
                            identity, symbol = workflow_identity(entry["file"], entry["symbol"])
                            labels[identity] = f"{entry['file']} · {symbol}"
                for group in report["groups"]:
                    group["label"] = labels.get(group["key"], group["key"])
            return report

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

    from cutback.server.operations import install_operations

    install_operations(app, sessions, authorize, scope, repository)
    from cutback.server.evaluation_api import install_evaluation

    import_dataset = install_evaluation(app, sessions, authorize, scope)

    from cutback.server.native_profile_api import install_native_profiles

    install_native_profiles(app, sessions, authorize, scope)

    from cutback.server.cache_workload_api import install_cache_workload

    install_cache_workload(app, sessions, authorize, scope)
    from cutback.server.campaign_api import install_campaigns

    install_campaigns(app, sessions, authorize, scope)
    from cutback.server.execution_api import install_execution

    install_execution(app, sessions, authorize, scope)
    from cutback.server.provider_batch_api import install_provider_batches

    install_provider_batches(app, sessions, authorize, scope)
    from cutback.server.economics_api import install_economics

    install_economics(app, sessions, authorize, scope)
    from cutback.server.improvement_api import install_improvements

    install_improvements(app, sessions, authorize, scope)
    from cutback.server.outcome_watch_api import install_outcome_watches

    install_outcome_watches(app, sessions, authorize, scope)
    from cutback.server.import_api import install_imports

    install_imports(app, sessions, authorize, scope)

    from cutback.server.review_api import install_reviews

    install_reviews(app, sessions, authorize, scope)

    from cutback.server.prompt_api import install_prompts

    install_prompts(app, sessions, authorize, scope)
    from cutback.server.monitoring_api import install_monitoring

    install_monitoring(app, sessions, authorize, scope)
    from cutback.server.notification_api import install_notifications

    install_notifications(app, sessions, authorize, scope)
    from cutback.server.quality_api import install_quality

    install_quality(app, sessions, authorize, scope)
    from cutback.server.gateway_api import install_gateway_controls

    install_gateway_controls(app, sessions, authorize, scope)
    from cutback.server.guardrail_api import install_guardrail_controls

    install_guardrail_controls(app, sessions, authorize, scope)
    from cutback.server.privacy_api import install_privacy

    install_privacy(app, sessions, authorize, scope)
    from cutback.server.organization_api import install_organizations

    install_organizations(app, sessions, authorize, scope)
    from cutback.server.continuous import install_continuous

    install_continuous(app, sessions, authorize, scope)
    from cutback.server.billing import install_billing

    install_billing(app, sessions, authorize, scope)
    from cutback.server.bootstrap_api import install_bootstrap_keys

    install_bootstrap_keys(app, sessions, authorize, scope)
    from cutback.server.cache_api import install_cache

    install_cache(app, sessions, authorize, scope)
    from cutback.server.semantic_cache_api import install_semantic_cache

    install_semantic_cache(app, sessions, authorize, scope)
    from cutback.server.semantic_upload_api import install_semantic_upload

    install_semantic_upload(app, sessions, authorize, scope)
    from cutback.server.training_api import install_training

    install_training(app, sessions, authorize, scope)
    from importlib.resources import files

    install_case_evidence(app, sessions, authorize, scope, import_dataset=import_dataset)
    from cutback.server.change_artifacts import install_changes

    install_changes(app, sessions, authorize, scope)
    from cutback.server.otel import install_otel

    install_otel(app, sessions, authorize, scope, repository)
    from cutback.server.traces import install_traces

    install_traces(app, sessions, authorize, scope)
    from fastapi.staticfiles import StaticFiles

    dashboard = files("cutback").joinpath("dashboard")
    if dashboard.joinpath("index.html").is_file():
        app.mount("/dashboard", StaticFiles(directory=str(dashboard), html=True), name="dashboard")
    return app
