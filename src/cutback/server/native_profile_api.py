"""Explicit owner-authorized request-profile analysis, with idempotent one-shot jobs."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field, field_validator, model_validator
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.server.db import AuditEvent, Environment
from cutback.server.evaluation_api import active_policy, require_store
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.native_profile_service import authority, public


class NativeProfileAnalysisInput(StrictModel):
    request_id: UUID4
    policy_id: UUID
    environment: Identifier
    workflow: Identifier
    purpose: Identifier
    window_start: datetime
    window_end: datetime
    max_observations: int = Field(default=1000, strict=True, ge=1, le=1000)
    max_wall_seconds: int = Field(default=60, strict=True, ge=5, le=300)

    @field_validator("window_start", "window_end")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("NATIVE_PROFILE_WINDOW_OFFSET_REQUIRED")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def bounded_window(self):
        if not timedelta(0) < self.window_end - self.window_start <= timedelta(days=1):
            raise ValueError("NATIVE_PROFILE_WINDOW_ONE_DAY_MAXIMUM")
        return self


def install_native_profiles(app, sessions, authorize, scope):
    authorized = Depends(authorize)

    def found(session, project, identifier):
        row = session.get(NativeProfileAnalysis, identifier)
        if not row or row.project_id != project:
            raise HTTPException(404, "NATIVE_PROFILE_ANALYSIS_NOT_FOUND")
        return row

    @app.post("/v1/projects/{project_id}/native-request-profiles/analyses", status_code=201)
    def queue(project_id: UUID, body: NativeProfileAnalysisInput, auth=authorized):
        scope(auth, project_id, ("admin",))
        require_store(app)
        if body.window_end > datetime.now(UTC):
            raise HTTPException(422, "NATIVE_PROFILE_FUTURE_WINDOW_DENIED")
        with sessions.begin() as session:
            from cutback.server.dataset_lineage import lock_lineage, lock_preparation_identity

            lock_lineage(session, project_id)
            _, policy = active_policy(session, project_id, body.policy_id)
            lock_preparation_identity(session, "native-profile-analysis", body.request_id)
            request_hash = digest(body.model_dump(mode="json"))
            previous = session.get(NativeProfileAnalysis, body.request_id, with_for_update=True)
            if previous:
                if (
                    previous.project_id != project_id
                    or previous.request_sha256 != request_hash
                    or previous.requester_actor != auth["actor"]
                ):
                    raise HTTPException(409, "NATIVE_PROFILE_REQUEST_ID_CONFLICT")
                try:
                    authority(session, previous)
                except ValueError:
                    raise HTTPException(409, "NATIVE_PROFILE_CURRENT_AUTHORITY_REQUIRED") from None
                return public(previous)
            environment = session.scalar(
                select(Environment).where(
                    Environment.project_id == project_id,
                    Environment.name == body.environment,
                )
            )
            if not environment or auth.get("environment_id") not in {None, environment.id}:
                raise HTTPException(403, "NATIVE_PROFILE_ENVIRONMENT_DENIED")
            # All policies for a project share this queue allowance under one project lock.
            count = session.scalar(
                select(func.count())
                .select_from(NativeProfileAnalysis)
                .where(
                    NativeProfileAnalysis.project_id == project_id,
                    NativeProfileAnalysis.expires_at > datetime.now(UTC),
                )
            )
            if count >= 100:
                raise HTTPException(429, "NATIVE_PROFILE_PROJECT_ANALYSIS_QUOTA")
            row = NativeProfileAnalysis(
                id=body.request_id,
                project_id=project_id,
                policy_id=body.policy_id,
                environment=body.environment,
                workflow=body.workflow,
                requester_actor=auth["actor"],
                purpose=body.purpose,
                request_sha256=request_hash,
                capture_policy_sha256=digest(policy.model_dump(mode="json")),
                window_start=body.window_start,
                window_end=body.window_end,
                max_observations=body.max_observations,
                max_wall_seconds=body.max_wall_seconds,
                expires_at=min(policy.expires_at, datetime.now(UTC) + timedelta(hours=1)),
                state="QUEUED",
                attempts=0,
            )
            try:
                authority(session, row)
            except ValueError:
                raise HTTPException(403, "NATIVE_PROFILE_CURRENT_AUTHORITY_REQUIRED") from None
            session.add(row)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="native_profile_analysis_queued",
                    details={"analysis_id": str(row.id), "purpose": body.purpose},
                )
            )
            session.flush()
            return public(row)

    @app.get("/v1/projects/{project_id}/native-request-profiles/analyses")
    def analyses(
        project_id: UUID,
        limit: int = Query(25, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=authorized,
    ):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return [
                public(row)
                for row in session.scalars(
                    select(NativeProfileAnalysis)
                    .where(
                        NativeProfileAnalysis.project_id == project_id,
                        NativeProfileAnalysis.expires_at > datetime.now(UTC),
                    )
                    .order_by(NativeProfileAnalysis.created_at.desc(), NativeProfileAnalysis.id)
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.get("/v1/projects/{project_id}/native-request-profiles/analyses/{analysis_id}")
    def analysis(project_id: UUID, analysis_id: UUID, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return public(found(session, project_id, analysis_id))

    @app.post("/v1/projects/{project_id}/native-request-profiles/analyses/{analysis_id}/cancel")
    def cancel(project_id: UUID, analysis_id: UUID, auth=authorized):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = found(session, project_id, analysis_id)
            # Cancellation remains available after the original content policy is disabled.
            from cutback.server.db import CapturePolicyRecord

            session.get(CapturePolicyRecord, row.policy_id, with_for_update=True)
            row = session.get(
                NativeProfileAnalysis,
                analysis_id,
                with_for_update=True,
                populate_existing=True,
            )
            if row.state in {"QUEUED", "RUNNING"}:
                row.state, row.summary, row.lease_until = "CANCELLED", None, None
                row.completed_at = datetime.now(UTC)
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="native_profile_analysis_cancelled",
                        details={"analysis_id": str(row.id)},
                    )
                )
            return public(row)
