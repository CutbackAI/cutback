"""Explicit encrypted preview -> reviewed immutable dataset. No evaluator execution."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from pydantic import Field, StrictBool
from sqlalchemy import select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import Case, DatasetImport, digest
from cutback.evals.importers import (
    ImportProfile,
    SourceImportError,
    assertion_provenance,
    prepare_import,
)
from cutback.server.content_policy import policy_content
from cutback.server.db import AuditEvent, DatasetRecord, PayloadSample
from cutback.server.evaluation_api import (
    ContentPurpose,
    active_policy,
    persist_dataset,
    policy_object_count,
    require_store,
)


class ImportPreviewInput(StrictModel):
    profile: ImportProfile
    source_text: str = Field(min_length=1, max_length=1048576)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_id: UUID
    environment: Identifier
    workflow: Identifier
    provenance_ref: Identifier


class ImportCommitInput(StrictModel):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_reviewed: StrictBool
    rights_confirmed: StrictBool
    accept_rejections: StrictBool = False


def install_imports(app, sessions, authorize, scope):
    def lookup(session, project, identifier, auth, *, content=False):
        row = session.get(PayloadSample, identifier)
        if (
            not row
            or row.project_id != project
            or row.metadata_safe.get("kind") != "import_preview"
        ):
            raise HTTPException(404, "IMPORT_PREVIEW_NOT_FOUND")
        if content:
            _, policy = active_policy(session, project, row.policy_id)
            row = session.get(
                PayloadSample, identifier, with_for_update=True, populate_existing=True
            )
            if row.expires_at <= datetime.now(UTC) or row.metadata_safe.get("deleted"):
                raise HTTPException(410, "IMPORT_PREVIEW_EXPIRED_OR_UNAVAILABLE")
            if not policy.permits(row.metadata_safe["environment"], row.metadata_safe["workflow"]):
                raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
            role = (
                auth.get("project_roles", {}).get(project)
                if auth["role"] == "oidc"
                else auth["role"]
            )
            if row.metadata_safe["creator"] != auth["actor"] and role not in {
                "admin",
                "project_admin",
            }:
                raise HTTPException(403, "IMPORT_PREVIEW_CREATOR_OR_ADMIN_REQUIRED")
            return row, policy
        return row

    def load(store, project, row, policy):
        try:
            value = json.loads(store.get(project, row.object_ref))
            if digest(value) != row.content_hash:
                raise ValueError("PREVIEW_INTEGRITY_FAILED")
            value, _ = policy_content(value, policy, preserve_semantics=True)
            return value
        except (OSError, ValueError, InvalidTag):
            raise HTTPException(410, "IMPORT_PREVIEW_EXPIRED_OR_UNAVAILABLE") from None

    @app.post("/v1/projects/{project_id}/imports/preview", status_code=201)
    def preview(project_id: UUID, body: ImportPreviewInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        refs: list[str] = []
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project_id, body.policy_id)
                if not policy.permits(body.environment, body.workflow):
                    raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                raw = body.source_text.encode("utf-8")
                if hashlib.sha256(raw).hexdigest() != body.source_sha256:
                    raise HTTPException(409, "IMPORT_SOURCE_HASH_CHANGED")
                try:
                    prepared = prepare_import(raw, body.profile)
                except SourceImportError as exc:
                    raise HTTPException(422, str(exc)) from None
                except (ValueError, TypeError):
                    raise HTTPException(422, "IMPORT_PROFILE_OR_SOURCE_INVALID") from None
                if not prepared.cases:
                    raise HTTPException(422, "IMPORT_HAS_NO_COMPATIBLE_CASES")
                if policy_object_count(session, body.policy_id) + 1 > policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                dataset = DatasetImport(
                    policy_id=str(body.policy_id),
                    environment=body.environment,
                    workflow=body.workflow,
                    provenance_ref=body.provenance_ref,
                    source_type="synthetic_fixture"
                    if policy.rights_basis == "synthetic"
                    else "application_records",
                    independent_labels=False,
                    representative=False,
                    cases=prepared.cases,
                )
                value = {
                    "dataset": dataset.model_dump(mode="json"),
                    "preview": prepared.preview(),
                    "assertions": prepared.assertions,
                }
                try:
                    clean, blob = policy_content(value, policy, preserve_semantics=True)
                except ValueError:
                    raise HTTPException(
                        422, "IMPORT_REQUIRES_APPROVED_DEIDENTIFIED_CONTENT_WITHIN_POLICY_BYTES"
                    ) from None
                ref = store.put(project_id, blob, policy.retention_days)
                refs.append(ref)
                row = PayloadSample(
                    project_id=project_id,
                    policy_id=body.policy_id,
                    object_ref=ref,
                    content_hash=digest(clean),
                    expires_at=min(
                        policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
                    ),
                    metadata_safe={
                        "kind": "import_preview",
                        "creator": auth["actor"],
                        "profile": body.profile,
                        "source_sha256": body.source_sha256,
                        "workflow": body.workflow,
                        "environment": body.environment,
                        "accepted": len(prepared.cases),
                        "rejected": len(prepared.rejected),
                        "state": "pending",
                        "summary": prepared.preview(),
                    },
                )
                session.add(row)
                session.flush()
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="import_preview_created",
                        details={
                            "preview_id": str(row.id),
                            "profile": body.profile,
                            "accepted": len(prepared.cases),
                            "rejected": len(prepared.rejected),
                        },
                    )
                )
            return {
                "id": str(row.id),
                "preview_hash": row.content_hash,
                **prepared.preview(),
                "content_uploaded": True,
                "content": "encrypted_pending_explicit_import",
                "dataset_created": False,
            }
        except BaseException:
            for ref in refs:
                store.delete(project_id, ref)
            raise

    @app.get("/v1/projects/{project_id}/imports")
    def previews(
        project_id: UUID,
        before: UUID | None = None,
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(PayloadSample).where(
                PayloadSample.project_id == project_id,
                PayloadSample.metadata_safe["kind"].as_string() == "import_preview",
            )
            if before:
                query = query.where(PayloadSample.id < before)
            rows = session.scalars(query.order_by(PayloadSample.id.desc()).limit(limit + 1)).all()
            return {
                "items": [
                    {
                        "id": str(r.id),
                        "preview_hash": r.content_hash,
                        "expires_at": r.expires_at.isoformat(),
                        **r.metadata_safe,
                    }
                    for r in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/imports/{preview_id}/content")
    def content(project_id: UUID, preview_id: UUID, body: ContentPurpose, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions.begin() as session:
            from sqlalchemy.dialects.postgresql import insert

            from cutback.server.dataset_lineage import (
                LineageViolation,
                lock_lineage,
                register_groups,
                require_unreserved_groups,
            )
            from cutback.server.lineage_db import DatasetExposure

            lock_lineage(session, project_id)
            row, policy = lookup(session, project_id, preview_id, auth, content=True)
            value = load(store, project_id, row, policy)
            cases = [Case.model_validate(c) for c in value["dataset"]["cases"][:25]]
            families = register_groups(session, project_id, cases, actor=auth["actor"])
            try:
                require_unreserved_groups(session, project_id, set(families.values()))
            except LineageViolation as exc:
                raise HTTPException(409, str(exc)) from None
            for family in set(families.values()):
                session.execute(
                    insert(DatasetExposure)
                    .values(
                        project_id=project_id,
                        family_id=family,
                        dataset_id=None,
                        run_id=None,
                        source_ref="import-preview-read:" + str(row.id),
                        mode="validation",
                        protected=False,
                    )
                    .on_conflict_do_nothing(constraint="uq_dataset_exposure")
                )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="import_preview_content_read",
                    details={"preview_id": str(row.id), "purpose": body.purpose},
                )
            )
            return {
                "cases": value["dataset"]["cases"][:25],
                "cases_truncated": len(value["dataset"]["cases"]) > 25,
                "source_asserted_checks": value["assertions"][:25],
                "checks_truncated": len(value["assertions"]) > 25,
                "independent_labels": False,
                "evidence": "externally_asserted",
            }

    @app.get("/v1/projects/{project_id}/imports/{preview_id}")
    def metadata(project_id: UUID, preview_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = lookup(session, project_id, preview_id, auth)
            return {
                **row.metadata_safe.get("summary", {}),
                "id": str(row.id),
                "preview_hash": row.content_hash,
                "state": row.metadata_safe["state"],
                "dataset_id": row.metadata_safe.get("dataset_id"),
                "expires_at": row.expires_at.isoformat(),
                "content_uploaded": True,
                "content": "encrypted_pending_explicit_import",
            }

    @app.post("/v1/projects/{project_id}/imports/{preview_id}/commit", status_code=201)
    def commit(
        project_id: UUID, preview_id: UUID, body: ImportCommitInput, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("analyst",))
        if not body.mapping_reviewed or not body.rights_confirmed:
            raise HTTPException(422, "IMPORT_MAPPING_AND_RIGHTS_CONFIRMATION_REQUIRED")
        store = require_store(app)
        refs: list[str] = []
        try:
            with sessions.begin() as session:
                from cutback.server.dataset_lineage import lock_lineage

                lock_lineage(session, project_id)
                row, policy = lookup(session, project_id, preview_id, auth, content=True)
                if row.content_hash != body.preview_hash:
                    raise HTTPException(409, "IMPORT_PREVIEW_HASH_CHANGED")
                if row.metadata_safe["rejected"] and not body.accept_rejections:
                    raise HTTPException(409, "IMPORT_REJECTIONS_REQUIRE_EXPLICIT_ACCEPTANCE")
                if row.metadata_safe["state"] == "imported":
                    dataset = session.get(DatasetRecord, UUID(row.metadata_safe["dataset_id"]))
                    return {
                        "id": str(dataset.id),
                        "immutable_hash": dataset.immutable_hash,
                        "status": "duplicate",
                        "provider_calls": 0,
                    }
                value = load(store, project_id, row, policy)
                result = persist_dataset(
                    session,
                    store,
                    project_id,
                    DatasetImport.model_validate(value["dataset"]),
                    auth["actor"],
                    refs,
                    provenance_extra={
                        "import_preview_id": str(row.id),
                        "source_profile": row.metadata_safe["profile"],
                        "source_sha256": row.metadata_safe["source_sha256"],
                        **assertion_provenance(value["assertions"]),
                        "source_rejections_accepted": row.metadata_safe["rejected"],
                        "independent_labels": False,
                    },
                )
                row.metadata_safe = {
                    **row.metadata_safe,
                    "state": "imported",
                    "dataset_id": result["id"],
                }
                return {**result, "provider_calls": 0}
        except BaseException:
            for ref in refs:
                store.delete(project_id, ref)
            raise
