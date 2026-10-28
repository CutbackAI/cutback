"""Encrypted prompt authoring and purpose-bound graph resolution; never invokes a model."""

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select

from cutback.core.prompts import PromptError, fingerprint
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AccessKey, AuditEvent, Environment, PayloadSample, Project
from cutback.server.evaluation_api import active_policy, require_store
from cutback.server.principals import principal_is_active, principal_role
from cutback.server.prompt_contracts import (
    PromptContext,
    PromptCreate,
    PromptGrantInput,
    PromptKey,
    PromptRender,
    PromptResolve,
    PromptVersionInput,
)
from cutback.server.prompt_db import (
    PromptAlias,
    PromptDependency,
    PromptReadGrant,
    PromptRecord,
    PromptVersion,
)
from cutback.server.prompt_service import (
    READ_ROLES,
    environment,
    load_graph,
    prompt_record,
    validate_composition,
    write_version_payload,
)


def version_metadata(session, row):
    payload = session.get(PayloadSample, row.payload_id)
    source_env = session.get(Environment, row.environment_id)
    return {
        "id": str(row.id),
        "prompt_id": str(row.prompt_id),
        "ordinal": row.ordinal,
        "document_hash": row.document_hash,
        "creator": row.creator,
        "environment_id": str(row.environment_id),
        "environment": source_env.name if source_env else None,
        "provenance_ref": row.provenance_ref,
        "created_at": row.created_at.isoformat(),
        "expires_at": payload.expires_at.isoformat() if payload else None,
        "content_reference_unexpired": bool(
            payload
            and payload.expires_at > datetime.now(UTC)
            and not payload.metadata_safe.get("deleted")
        ),
        "dependencies": [
            {"alias": r.alias, "version_id": str(r.dependency_id)}
            for r in session.scalars(
                select(PromptDependency)
                .where(PromptDependency.version_id == row.id)
                .order_by(PromptDependency.alias)
            )
        ],
    }


def record_metadata(row):
    return {
        "id": str(row.id),
        "key": row.key,
        "workflow": row.workflow,
        "creator": row.creator,
        "created_at": row.created_at.isoformat(),
    }


def grant_metadata(row):
    return {
        "id": str(row.id),
        "actor": row.actor,
        "prompt_id": str(row.prompt_id),
        "environment_id": str(row.environment_id),
        "workflow": row.workflow,
        "policy_id": str(row.policy_id),
        "expires_at": row.expires_at.isoformat(),
        "enabled": row.enabled,
        "revision": row.revision,
        "approval_ref": row.approval_ref,
        "approved_by": row.approved_by,
    }


def audit(session, project, auth, action, **details):
    session.add(AuditEvent(project_id=project, actor=auth["actor"], action=action, details=details))


def current_control(session, project, auth, *, admin=False):
    permitted = (
        {"admin", "project_admin"} if admin else {"analyst", "editor", "project_admin", "admin"}
    )
    if principal_role(session, project, auth["actor"]) not in permitted:
        raise HTTPException(403, "CURRENT_PROMPT_CONTROL_ROLE_REQUIRED")


def install_prompts(app, sessions, authorize, scope):
    from cutback.server.prompt_observations import install_prompt_observations

    install_prompt_observations(app, sessions, authorize, scope)

    @contextmanager
    def transaction(project):
        store = require_store(app)
        refs: list[str] = []
        try:
            with sessions.begin() as session:
                lock_lineage(session, project)
                yield session, store, refs
        except PromptError as exc:
            for ref in refs:
                store.delete(project, ref)
            raise HTTPException(422, str(exc)) from None
        except BaseException:
            for ref in refs:
                store.delete(project, ref)
            raise

    @app.get("/v1/projects/{project_id}/prompts")
    def prompts(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(PromptRecord).where(PromptRecord.project_id == project_id)
            if after:
                query = query.where(PromptRecord.id > after)
            rows = session.scalars(query.order_by(PromptRecord.id).limit(limit + 1)).all()
            return {
                "items": [record_metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/prompts", status_code=201)
    def create(project_id: UUID, body: PromptCreate, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            current_control(session, project_id, auth)
            if not session.get(Project, project_id):
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            existing = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.project_id == project_id, PromptRecord.key == body.key
                )
            )
            if existing:
                if existing.workflow != body.workflow:
                    raise HTTPException(409, "PROMPT_KEY_WORKFLOW_CONFLICT")
                return {**record_metadata(existing), "status": "duplicate"}
            count = session.scalar(
                select(func.count())
                .select_from(PromptRecord)
                .where(PromptRecord.project_id == project_id)
            )
            if count >= 10000:
                raise HTTPException(429, "PROJECT_PROMPT_COUNT_BOUND_EXCEEDED")
            row = PromptRecord(
                project_id=project_id, key=body.key, workflow=body.workflow, creator=auth["actor"]
            )
            session.add(row)
            session.flush()
            audit(session, project_id, auth, "prompt_created", prompt_id=str(row.id))
            return record_metadata(row)

    @app.get("/v1/projects/{project_id}/prompts/{prompt_id}/versions")
    def versions(
        project_id: UUID,
        prompt_id: UUID,
        before: int | None = Query(None, ge=1),
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            prompt_record(session, project_id, prompt_id)
            query = select(PromptVersion).where(
                PromptVersion.prompt_id == prompt_id, PromptVersion.project_id == project_id
            )
            if before:
                query = query.where(PromptVersion.ordinal < before)
            rows = session.scalars(
                query.order_by(PromptVersion.ordinal.desc()).limit(limit + 1)
            ).all()
            return {
                "items": [version_metadata(session, r) for r in rows[:limit]],
                "next_cursor": rows[limit - 1].ordinal if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/versions", status_code=201)
    def create_version(
        project_id: UUID, prompt_id: UUID, body: PromptVersionInput, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, refs):
            current_control(session, project_id, auth)
            record = prompt_record(session, project_id, prompt_id)
            env = environment(session, project_id, body.environment)
            _, policy = active_policy(session, project_id, body.policy_id)
            if not policy.permits(env.name, record.workflow):
                raise HTTPException(403, "PROMPT_CAPTURE_SCOPE_DENIED")
            request_hash = fingerprint(body.model_dump(mode="json"))
            existing = session.get(PromptVersion, body.version_id)
            if existing:
                if (
                    existing.project_id != project_id
                    or existing.prompt_id != prompt_id
                    or existing.creator != auth["actor"]
                ):
                    raise HTTPException(409, "PROMPT_VERSION_IMMUTABLE_IDENTITY_CONFLICT")
                loaded = load_graph(
                    session,
                    store,
                    project_id,
                    existing.id,
                    auth,
                    env_name=env.name,
                    workflow=record.workflow,
                    purpose="verify-idempotent-prompt-version",
                )
                if loaded.content_receipts[existing.id]["request_fingerprint"] != request_hash:
                    raise HTTPException(409, "PROMPT_VERSION_IMMUTABLE_IDENTITY_CONFLICT")
                return {**version_metadata(session, existing), "status": "duplicate"}
            latest = (
                session.scalar(
                    select(func.max(PromptVersion.ordinal)).where(
                        PromptVersion.prompt_id == prompt_id
                    )
                )
                or 0
            )
            if latest != body.expected_latest_ordinal:
                raise HTTPException(409, "PROMPT_VERSION_HEAD_CHANGED")
            if latest >= 10000:
                raise HTTPException(429, "PROMPT_VERSION_COUNT_BOUND_EXCEEDED")
            validate_composition(
                session,
                store,
                project_id,
                body.version_id,
                body.document,
                auth,
                env_name=env.name,
                workflow=record.workflow,
            )
            payload = write_version_payload(
                session,
                store,
                project_id,
                body.policy_id,
                policy,
                body.document,
                refs,
                request_fingerprint=request_hash,
            )
            row = PromptVersion(
                id=body.version_id,
                project_id=project_id,
                prompt_id=prompt_id,
                environment_id=env.id,
                ordinal=latest + 1,
                payload_id=payload.id,
                document_hash=payload.content_hash,
                creator=auth["actor"],
                provenance_ref=body.provenance_ref,
            )
            session.add(row)
            session.flush()
            for alias, identifier in body.document.dependencies.items():
                session.add(
                    PromptDependency(
                        project_id=project_id,
                        version_id=row.id,
                        alias=alias,
                        dependency_id=identifier,
                    )
                )
            audit(
                session,
                project_id,
                auth,
                "prompt_version_created",
                prompt_id=str(prompt_id),
                version_id=str(row.id),
                ordinal=row.ordinal,
            )
            return version_metadata(session, row)

    def pinned(session, project, prompt_id, version_id):
        prompt_record(session, project, prompt_id)
        row = session.get(PromptVersion, version_id)
        if not row or row.project_id != project or row.prompt_id != prompt_id:
            raise HTTPException(404, "PINNED_PROMPT_VERSION_NOT_FOUND")
        return row

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/versions/{version_id}/content")
    def content(
        project_id: UUID,
        prompt_id: UUID,
        version_id: UUID,
        body: PromptContext,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with transaction(project_id) as (session, store, _):
            pinned(session, project_id, prompt_id, version_id)
            return load_graph(
                session,
                store,
                project_id,
                version_id,
                auth,
                env_name=body.environment,
                workflow=body.workflow,
                purpose=body.purpose,
            ).response()

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/versions/{version_id}/preview")
    def preview(
        project_id: UUID,
        prompt_id: UUID,
        version_id: UUID,
        body: PromptRender,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        if not body.values_authorized:
            raise HTTPException(422, "PROMPT_PREVIEW_VALUES_AUTHORIZATION_REQUIRED")
        with transaction(project_id) as (session, store, _):
            pinned(session, project_id, prompt_id, version_id)
            loaded = load_graph(
                session,
                store,
                project_id,
                version_id,
                auth,
                env_name=body.environment,
                workflow=body.workflow,
                purpose=body.purpose,
            )
            from cutback.server.content_policy import policy_content

            for policy in loaded.policies.values():
                try:
                    policy_content({"variables": body.variables}, policy, preserve_semantics=True)
                except ValueError:
                    raise HTTPException(422, "PROMPT_PREVIEW_VALUES_OUTSIDE_POLICY") from None
            return {
                "rendered": loaded.graph.render(body.variables),
                "version_hash": loaded.versions[version_id].document_hash,
                "values_retained": False,
                "provider_calls": 0,
            }

    @app.post("/v1/projects/{project_id}/prompts/resolve/{key}")
    def resolve(project_id: UUID, key: PromptKey, body: PromptResolve, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst", "prompt_reader"))
        with transaction(project_id) as (session, store, _):
            record = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.project_id == project_id, PromptRecord.key == key
                )
            )
            if not record:
                raise HTTPException(404, "PROMPT_NOT_FOUND")
            env = environment(session, project_id, body.environment)
            alias = None
            version_id = body.version_id
            if body.alias is not None:
                alias = session.scalar(
                    select(PromptAlias).where(
                        PromptAlias.prompt_id == record.id,
                        PromptAlias.environment_id == env.id,
                        PromptAlias.name == body.alias,
                    )
                )
                if not alias:
                    raise HTTPException(404, "PROMPT_ALIAS_NOT_FOUND")
                version_id = alias.version_id
            pinned(session, project_id, record.id, version_id)
            loaded = load_graph(
                session,
                store,
                project_id,
                version_id,
                auth,
                env_name=body.environment,
                workflow=body.workflow,
                provider=body.provider,
                purpose=body.purpose,
            )
            return {
                **loaded.response(),
                "prompt_id": str(record.id),
                "key": record.key,
                "alias": {"id": str(alias.id), "name": alias.name, "revision": alias.revision}
                if alias
                else None,
                "usage_coverage": "unknown",
                "variables_uploaded": False,
            }

    @app.get("/v1/projects/{project_id}/prompt-read-grants")
    def grants(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            query = select(PromptReadGrant).where(PromptReadGrant.project_id == project_id)
            if after:
                query = query.where(PromptReadGrant.id > after)
            rows = session.scalars(query.order_by(PromptReadGrant.id).limit(limit + 1)).all()
            return {
                "items": [grant_metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/prompts/{prompt_id}/read-grants", status_code=201)
    def grant(project_id: UUID, prompt_id: UUID, body: PromptGrantInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            current_control(session, project_id, auth, admin=True)
            record = prompt_record(session, project_id, prompt_id)
            env = environment(session, project_id, body.environment)
            _, policy = active_policy(session, project_id, body.policy_id)
            if not policy.permits(env.name, record.workflow) or not policy.permits(
                env.name, body.workflow
            ):
                raise HTTPException(403, "PROMPT_GRANT_POLICY_SCOPE_DENIED")
            if not principal_is_active(session, project_id, body.actor, roles=READ_ROLES):
                raise HTTPException(422, "PROMPT_GRANT_CURRENT_PRINCIPAL_REQUIRED")
            if body.expires_at <= datetime.now(UTC) or body.expires_at > policy.expires_at:
                raise HTTPException(422, "PROMPT_GRANT_EXPIRY_OUTSIDE_POLICY")
            if not body.actor.startswith("oidc:"):
                key_row = session.get(AccessKey, UUID(body.actor))
                if key_row.role == "prompt_reader" and key_row.environment_id != env.id:
                    raise HTTPException(403, "PROMPT_READER_ENVIRONMENT_SCOPE_DENIED")
                if key_row.expires_at and body.expires_at > key_row.expires_at:
                    raise HTTPException(422, "PROMPT_GRANT_EXPIRY_EXCEEDS_CREDENTIAL")
            row = session.scalar(
                select(PromptReadGrant)
                .where(
                    PromptReadGrant.project_id == project_id,
                    PromptReadGrant.actor == body.actor,
                    PromptReadGrant.prompt_id == prompt_id,
                    PromptReadGrant.environment_id == env.id,
                    PromptReadGrant.workflow == body.workflow,
                    PromptReadGrant.policy_id == body.policy_id,
                )
                .with_for_update()
            )
            if row:
                if (
                    row.revision == body.expected_revision + 1
                    and row.enabled
                    and row.expires_at == body.expires_at
                    and row.approval_ref == body.approval_ref
                ):
                    return {**grant_metadata(row), "status": "duplicate"}
                if row.revision != body.expected_revision:
                    raise HTTPException(409, "PROMPT_GRANT_REVISION_CHANGED")
                row.enabled, row.expires_at = True, body.expires_at
                row.approved_by, row.approval_ref = auth["actor"], body.approval_ref
                row.revision += 1
            else:
                if body.expected_revision != 0:
                    raise HTTPException(409, "PROMPT_GRANT_REVISION_CHANGED")
                if (
                    session.scalar(
                        select(func.count())
                        .select_from(PromptReadGrant)
                        .where(PromptReadGrant.project_id == project_id)
                    )
                    >= 10000
                ):
                    raise HTTPException(429, "PROMPT_READ_GRANT_COUNT_BOUND_EXCEEDED")
                row = PromptReadGrant(
                    project_id=project_id,
                    actor=body.actor,
                    prompt_id=prompt_id,
                    environment_id=env.id,
                    workflow=body.workflow,
                    policy_id=body.policy_id,
                    expires_at=body.expires_at,
                    approved_by=auth["actor"],
                    approval_ref=body.approval_ref,
                )
                session.add(row)
                session.flush()
            audit(
                session,
                project_id,
                auth,
                "prompt_read_grant_changed",
                grant_id=str(row.id),
                revision=row.revision,
                approval_ref=body.approval_ref,
            )
            return grant_metadata(row)

    @app.delete("/v1/projects/{project_id}/prompt-read-grants/{grant_id}")
    def revoke(project_id: UUID, grant_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            current_control(session, project_id, auth, admin=True)
            row = session.get(PromptReadGrant, grant_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "PROMPT_READ_GRANT_NOT_FOUND")
            if row.enabled:
                row.enabled = False
                row.revision += 1
                audit(
                    session,
                    project_id,
                    auth,
                    "prompt_read_grant_revoked",
                    grant_id=str(row.id),
                    revision=row.revision,
                )
            return {"enabled": False, "revision": row.revision}

    from cutback.server.prompt_alias_api import install_prompt_aliases

    install_prompt_aliases(app, sessions, authorize, scope, transaction)
