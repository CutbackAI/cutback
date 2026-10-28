"""Audited team administration, project settings and OIDC/PKCE browser entry points."""

import hashlib
import os
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cutback.core.config import validate_endpoint
from cutback.core.models import StrictModel
from cutback.server.db import AuditEvent, Organization, Project
from cutback.server.enterprise_db import OrganizationMember, ProjectGrant, ProjectSettings
from cutback.server.identity import exchange_login, login_configuration, start_login, subject_digest


class OrganizationInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)


class MemberInput(StrictModel):
    issuer: str = Field(max_length=500)
    subject: str = Field(min_length=1, max_length=200)
    role: Literal["owner", "admin", "member"] = "member"


class GrantInput(StrictModel):
    project_id: UUID
    role: Literal["viewer", "analyst", "editor", "project_admin", "prompt_reader"] = "viewer"


class RetentionInput(StrictModel):
    raw_days: int = Field(ge=1, le=395)
    aggregate_days: int = Field(ge=1, le=3650)

    @model_validator(mode="after")
    def valid_periods(self):
        if self.raw_days > self.aggregate_days:
            raise ValueError("raw retention cannot exceed aggregate retention")
        return self


class LoginStart(StrictModel):
    challenge: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]


class LoginExchange(StrictModel):
    state: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
    verifier: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.~-]{43,128}$")]
    code: str = Field(min_length=1, max_length=4096)


def organization_scope(auth, organization_id, *, manage=False, owner=False):
    if auth["role"] == "admin":
        return
    role = auth.get("organizations", {}).get(organization_id)
    if not role or (manage and role not in {"owner", "admin"}) or (owner and role != "owner"):
        raise HTTPException(403, "ORGANIZATION_SCOPE_DENIED")


def audit(session, auth, action, organization_id, details=None, project_id=None):
    session.add(
        AuditEvent(
            actor=auth["actor"],
            action=action,
            project_id=project_id,
            details={"organization_id": str(organization_id), **(details or {})},
        )
    )


def install_organizations(app, sessions, authorize, scope):
    @app.get("/v1/auth/config")
    def auth_configuration():
        return login_configuration(app.state.oidc_verifier)

    @app.post("/v1/auth/oidc/start")
    def login_start(body: LoginStart, request: Request):
        from cutback.server.operations import enforce_quota

        enforce_quota(
            sessions,
            hashlib.sha256(
                ("login:" + (request.client.host if request.client else "unknown")).encode()
            ).hexdigest(),
            20,
        )
        return start_login(app.state.oidc_verifier, sessions, body.challenge)

    @app.post("/v1/auth/oidc/exchange")
    def login_exchange(body: LoginExchange):
        return exchange_login(app.state.oidc_verifier, sessions, body)

    @app.get("/v1/me")
    def me(auth=Depends(authorize)):
        return {
            "role": auth["role"],
            "actor": auth["actor"],
            "project_id": str(auth["project_id"]) if auth.get("project_id") else None,
            "project_roles": {
                str(key): role for key, role in auth.get("project_roles", {}).items()
            },
            "organizations": {
                str(key): role for key, role in auth.get("organizations", {}).items()
            },
            "bootstrap": auth["role"] == "admin",
        }

    @app.get("/v1/organizations")
    def organizations(auth=Depends(authorize)):
        if auth["role"] not in {"admin", "oidc"}:
            raise HTTPException(403, "ORGANIZATION_SCOPE_DENIED")
        query = select(Organization).order_by(Organization.id).limit(100)
        if auth["role"] == "oidc":
            query = query.where(Organization.id.in_(auth["organizations"]))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "name": row.name,
                    "role": auth.get("organizations", {}).get(row.id, "installation_admin"),
                }
                for row in session.scalars(query)
            ]

    @app.post("/v1/organizations", status_code=201)
    def create_organization(body: OrganizationInput, auth=Depends(authorize)):
        scope(auth, None, ("admin",))
        with sessions.begin() as session:
            row = Organization(name=body.name)
            session.add(row)
            session.flush()
            audit(session, auth, "organization_created", row.id)
        return {"id": str(row.id), "name": row.name}

    @app.get("/v1/organizations/{organization_id}/members")
    def members(organization_id: UUID, auth=Depends(authorize)):
        organization_scope(auth, organization_id, manage=True)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "subject_hash": row.subject_hash,
                    "issuer": row.issuer,
                    "role": row.role,
                    "enabled": row.enabled,
                    "grants": [
                        {"project_id": str(grant.project_id), "role": grant.role}
                        for grant in session.scalars(
                            select(ProjectGrant).where(ProjectGrant.member_id == row.id)
                        )
                    ],
                }
                for row in session.scalars(
                    select(OrganizationMember)
                    .where(OrganizationMember.organization_id == organization_id)
                    .limit(1000)
                )
            ]

    @app.post("/v1/organizations/{organization_id}/members", status_code=201)
    def add_member(organization_id: UUID, body: MemberInput, auth=Depends(authorize)):
        organization_scope(auth, organization_id, manage=True, owner=body.role != "member")
        try:
            validate_endpoint(body.issuer)
        except ValueError:
            raise HTTPException(422, "INVALID_IDENTITY_ISSUER") from None
        with sessions.begin() as session:
            if not session.get(Organization, organization_id):
                raise HTTPException(404, "ORGANIZATION_NOT_FOUND")
            row = OrganizationMember(
                organization_id=organization_id,
                issuer=body.issuer,
                subject_hash=subject_digest(body.issuer, body.subject),
                role=body.role,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                raise HTTPException(409, "MEMBERSHIP_EXISTS") from None
            audit(
                session,
                auth,
                "member_added",
                organization_id,
                {"member_id": str(row.id), "role": row.role},
            )
        return {"id": str(row.id), "subject_hash": row.subject_hash, "role": row.role}

    @app.delete("/v1/organizations/{organization_id}/members/{member_id}")
    def remove_member(organization_id: UUID, member_id: UUID, auth=Depends(authorize)):
        organization_scope(auth, organization_id, manage=True)
        with sessions.begin() as session:
            session.get(Organization, organization_id, with_for_update=True)
            row = session.get(OrganizationMember, member_id, with_for_update=True)
            if not row or row.organization_id != organization_id:
                raise HTTPException(404, "MEMBER_NOT_FOUND")
            if row.role != "member":
                organization_scope(auth, organization_id, owner=True)
            if (
                row.role == "owner"
                and session.scalar(
                    select(func.count())
                    .select_from(OrganizationMember)
                    .where(
                        OrganizationMember.organization_id == organization_id,
                        OrganizationMember.role == "owner",
                        OrganizationMember.enabled.is_(True),
                    )
                )
                <= 1
            ):
                raise HTTPException(409, "LAST_OWNER_CANNOT_BE_REMOVED")
            row.enabled = False
            audit(session, auth, "member_disabled", organization_id, {"member_id": str(row.id)})
        return {"status": "disabled"}

    @app.post("/v1/organizations/{organization_id}/members/{member_id}/grants")
    def grant(organization_id: UUID, member_id: UUID, body: GrantInput, auth=Depends(authorize)):
        organization_scope(auth, organization_id, manage=True)
        with sessions.begin() as session:
            member, project = (
                session.get(OrganizationMember, member_id),
                session.get(Project, body.project_id),
            )
            if (
                not member
                or not project
                or member.organization_id != organization_id
                or project.organization_id != organization_id
            ):
                raise HTTPException(404, "MEMBER_OR_PROJECT_NOT_IN_ORGANIZATION")
            row = session.scalar(
                select(ProjectGrant)
                .where(
                    ProjectGrant.member_id == member_id, ProjectGrant.project_id == body.project_id
                )
                .with_for_update()
            )
            if row:
                row.role = body.role
            else:
                session.add(
                    ProjectGrant(member_id=member_id, project_id=body.project_id, role=body.role)
                )
            audit(
                session,
                auth,
                "project_grant_updated",
                organization_id,
                {"member_id": str(member_id), "role": body.role},
                body.project_id,
            )
        return {"status": "granted"}

    @app.delete("/v1/organizations/{organization_id}/members/{member_id}/grants/{project_id}")
    def revoke_grant(
        organization_id: UUID, member_id: UUID, project_id: UUID, auth=Depends(authorize)
    ):
        organization_scope(auth, organization_id, manage=True)
        with sessions.begin() as session:
            member = session.get(OrganizationMember, member_id)
            project = session.get(Project, project_id)
            if (
                not member
                or member.organization_id != organization_id
                or not project
                or project.organization_id != organization_id
            ):
                raise HTTPException(404, "MEMBER_NOT_FOUND")
            row = session.scalar(
                select(ProjectGrant).where(
                    ProjectGrant.member_id == member_id, ProjectGrant.project_id == project_id
                )
            )
            if row:
                session.delete(row)
            audit(
                session,
                auth,
                "project_grant_revoked",
                organization_id,
                {"member_id": str(member_id)},
                project_id,
            )
        return {"status": "revoked"}

    @app.get("/v1/projects/{project_id}/settings")
    def settings(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            row = session.get(ProjectSettings, project_id)
            return {
                "raw_days": row.raw_days if row else int(os.getenv("CUTBACK_RAW_DAYS", "30")),
                "aggregate_days": row.aggregate_days
                if row
                else int(os.getenv("CUTBACK_AGGREGATE_DAYS", "395")),
                "capture_enabled_globally": os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower()
                == "true",
                "inference_enabled_globally": os.getenv(
                    "CUTBACK_EXPERIMENT_EXECUTION", "false"
                ).lower()
                == "true",
            }

    @app.put("/v1/projects/{project_id}/settings")
    def update_settings(project_id: UUID, body: RetentionInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            project = session.get(Project, project_id)
            if not project:
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            row = session.get(ProjectSettings, project_id, with_for_update=True)
            if not row:
                row = ProjectSettings(project_id=project_id)
                session.add(row)
            row.raw_days, row.aggregate_days, row.updated_at = (
                body.raw_days,
                body.aggregate_days,
                datetime.now(UTC),
            )
            audit(
                session,
                auth,
                "retention_changed",
                project.organization_id,
                body.model_dump(),
                project_id,
            )
        return {
            "status": "saved",
            **body.model_dump(),
            "deletion": "next authorized maintenance cycle",
        }
