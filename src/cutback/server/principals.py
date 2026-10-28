"""Live project principal checks shared by explicit content assignments and grants."""

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from cutback.server.db import AccessKey, Project
from cutback.server.enterprise_db import OrganizationMember, ProjectGrant


def principal_role(session, project, actor):
    if actor == "bootstrap_admin":
        return "admin"
    if re.fullmatch(r"oidc:[a-f0-9]{32}", actor):
        target = session.get(Project, project)
        if not target:
            return None
        members = session.scalars(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == target.organization_id,
                OrganizationMember.enabled.is_(True),
                OrganizationMember.subject_hash.startswith(actor[5:]),
            )
        ).all()
        if len(members) != 1:
            return None
        member = members[0]
        if member.role in {"owner", "admin"}:
            return "project_admin"
        grant = session.scalar(
            select(ProjectGrant).where(
                ProjectGrant.member_id == member.id, ProjectGrant.project_id == project
            )
        )
        return grant.role if grant else None
    try:
        if str(UUID(actor)) != actor:
            return None
        key = session.get(AccessKey, UUID(actor))
    except ValueError:
        return None
    live = bool(
        key
        and key.project_id == project
        and not key.revoked_at
        and (not key.expires_at or key.expires_at > datetime.now(UTC))
    )
    return key.role if live and key else None


def principal_is_active(session, project, actor, *, roles):
    return principal_role(session, project, actor) in roles
