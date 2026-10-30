"""Resumable encrypted case uploads with a frozen sequence and shared quota reservations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import func, select

from cutback.evals.datasets import digest
from cutback.server.campaign_content import content_transaction
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, CapturePolicyRecord, PayloadSample
from cutback.server.evaluation_api import policy_object_count, require_store
from cutback.server.semantic_cache_api import enabled, failure
from cutback.server.semantic_cache_service import authority, namespace
from cutback.server.semantic_upload_db import SemanticDatasetUpload, SemanticUploadCase
from cutback.server.semantic_upload_models import (
    SemanticUploadAppend,
    SemanticUploadPrepare,
    SemanticUploadReceipt,
    SemanticUploadSeal,
)
from cutback.server.semantic_upload_service import upload_authority


def upload_receipt(row):
    return {
        name: getattr(row, name)
        for name in (
            "id",
            "namespace_id",
            "state",
            "approval_hash",
            "specification",
            "received_cases",
            "received_bytes",
            "dataset_id",
            "attempts",
            "uncertain_attempts",
            "failure_code",
            "expires_at",
        )
    }


def owned_upload(session, project, identifier, auth):
    lock_lineage(session, project)
    row = session.get(
        SemanticDatasetUpload, identifier, with_for_update=True, populate_existing=True
    )
    if not row or row.project_id != project:
        raise HTTPException(404, "SEMANTIC_UPLOAD_NOT_FOUND")
    if row.created_by != auth["actor"] and auth["role"] not in {"admin", "project_admin"}:
        raise HTTPException(403, "SEMANTIC_UPLOAD_CREATOR_OR_ADMIN_REQUIRED")
    return row


def audit(session, row, actor, action):
    session.add(
        AuditEvent(
            project_id=row.project_id,
            actor=actor,
            action=action,
            details={"upload_id": str(row.id), "received_cases": row.received_cases},
        )
    )


def install_semantic_upload(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/semantic-cache/namespaces/{namespace_id}/dataset-uploads",
        response_model=SemanticUploadReceipt,
        status_code=201,
    )
    def prepare(
        project_id: UUID, namespace_id: UUID, body: SemanticUploadPrepare, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("admin", "analyst"))
        require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            try:
                ns = namespace(session, project_id, namespace_id, lock=True)
                spec, policy, key = authority(session, ns)
                if (
                    body.metadata.workflow != spec.workflow
                    or body.metadata.environment != spec.environment
                    or body.metadata.policy_id != ns.policy_id
                ):
                    raise ValueError("SEMANTIC_UPLOAD_METADATA_SCOPE_MISMATCH")
            except ValueError as exc:
                raise failure(exc) from None
            session.get(CapturePolicyRecord, ns.policy_id, with_for_update=True)
            if (
                policy_object_count(session, ns.policy_id) + body.expected_cases
                > policy.max_samples
            ):
                raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
            if (
                session.scalar(
                    select(func.count())
                    .select_from(SemanticDatasetUpload)
                    .where(SemanticDatasetUpload.project_id == project_id)
                )
                or 0
            ) >= 100:
                raise HTTPException(429, "SEMANTIC_UPLOAD_PROJECT_LIMIT")
            if (
                body.metadata.source_type == "synthetic_fixture"
                and policy.rights_basis != "synthetic"
            ):
                raise HTTPException(422, "SYNTHETIC_PROVENANCE_POLICY_MISMATCH")
            frozen = {
                "upload": body.model_dump(mode="json"),
                "namespace_hash": ns.specification_hash,
                "capture_policy_hash": digest(policy.model_dump(mode="json")),
            }
            row = SemanticDatasetUpload(
                project_id=project_id,
                namespace_id=ns.id,
                policy_id=ns.policy_id,
                specification=frozen,
                approval_hash=digest(frozen),
                state="STAGING",
                expected_cases=body.expected_cases,
                max_bytes=body.max_bytes,
                received_cases=0,
                received_bytes=0,
                attempts=0,
                uncertain_attempts=0,
                created_by=auth["actor"],
                expires_at=min(
                    datetime.now(UTC) + timedelta(hours=1),
                    ns.expires_at,
                    policy.expires_at,
                    key.expires_at or ns.expires_at,
                ),
            )
            session.add(row)
            session.flush()
            audit(session, row, auth["actor"], "semantic_dataset_upload_prepared")
            return upload_receipt(row)

    @app.get(
        "/v1/projects/{project_id}/semantic-cache/dataset-uploads/{upload_id}",
        response_model=SemanticUploadReceipt,
    )
    def show(project_id: UUID, upload_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(SemanticDatasetUpload, upload_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "SEMANTIC_UPLOAD_NOT_FOUND")
            return upload_receipt(row)

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/dataset-uploads/{upload_id}/append",
        response_model=SemanticUploadReceipt,
    )
    def append(
        project_id: UUID, upload_id: UUID, body: SemanticUploadAppend, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("admin", "analyst"))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            row = owned_upload(session, project_id, upload_id, auth)
            try:
                _, policy, _ = upload_authority(session, row)
            except ValueError as exc:
                raise failure(exc) from None
            if row.state != "STAGING" or body.approval_hash != row.approval_hash:
                raise HTTPException(409, "SEMANTIC_UPLOAD_STATE_OR_APPROVAL_MISMATCH")
            session.get(CapturePolicyRecord, row.policy_id, with_for_update=True)
            if policy_object_count(session, row.policy_id) > policy.max_samples:
                raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
            blobs = []
            for case in body.cases:
                try:
                    _, blob = policy_content(
                        case.model_dump(mode="json"), policy, preserve_semantics=True
                    )
                except ValueError as exc:
                    raise failure(exc, 422) from None
                blobs.append((digest(case.model_dump(mode="json")), blob))
            if body.offset < row.received_cases:
                records = session.scalars(
                    select(SemanticUploadCase)
                    .where(
                        SemanticUploadCase.upload_id == row.id,
                        SemanticUploadCase.project_id == project_id,
                        SemanticUploadCase.ordinal >= body.offset,
                        SemanticUploadCase.ordinal < body.offset + len(blobs),
                    )
                    .order_by(SemanticUploadCase.ordinal)
                ).all()
                if len(records) != len(blobs) or any(
                    record.content_hash != value[0] or record.byte_count != len(value[1])
                    for record, value in zip(records, blobs, strict=True)
                ):
                    raise HTTPException(409, "SEMANTIC_UPLOAD_OFFSET_CONTENT_CONFLICT")
                return upload_receipt(row)
            if (
                body.offset != row.received_cases
                or body.offset + len(blobs) > row.expected_cases
                or row.received_bytes + sum(len(blob) for _, blob in blobs) > row.max_bytes
            ):
                raise HTTPException(409, "SEMANTIC_UPLOAD_OFFSET_OR_TOTAL_BOUND")
            for index, (content_hash, blob) in enumerate(blobs):
                ref = store.put(project_id, blob, policy.retention_days)
                refs.append(ref)
                payload = PayloadSample(
                    project_id=project_id,
                    policy_id=row.policy_id,
                    object_ref=ref,
                    content_hash=content_hash,
                    expires_at=row.expires_at,
                    metadata_safe={
                        "kind": "semantic_upload_case",
                        "upload_id": str(row.id),
                        "ordinal": body.offset + index,
                        "redaction": policy.redaction_version,
                    },
                )
                session.add(payload)
                session.flush()
                session.add(
                    SemanticUploadCase(
                        project_id=project_id,
                        upload_id=row.id,
                        ordinal=body.offset + index,
                        payload_id=payload.id,
                        content_hash=content_hash,
                        byte_count=len(blob),
                    )
                )
            row.received_cases += len(blobs)
            row.received_bytes += sum(len(blob) for _, blob in blobs)
            audit(session, row, auth["actor"], "semantic_dataset_upload_appended")
            return upload_receipt(row)

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/dataset-uploads/{upload_id}/finalize",
        response_model=SemanticUploadReceipt,
        status_code=202,
    )
    def finalize(
        project_id: UUID, upload_id: UUID, body: SemanticUploadSeal, auth=Depends(authorize)
    ):
        enabled()
        scope(auth, project_id, ("admin", "analyst"))
        require_store(app)
        with sessions.begin() as session:
            row = owned_upload(session, project_id, upload_id, auth)
            if body.approval_hash != row.approval_hash:
                raise HTTPException(409, "SEMANTIC_UPLOAD_APPROVAL_MISMATCH")
            if row.state in {"FINALIZING", "FINALIZED"}:
                return upload_receipt(row)
            try:
                upload_authority(session, row)
            except ValueError as exc:
                raise failure(exc) from None
            if row.state != "STAGING" or row.received_cases != row.expected_cases:
                raise HTTPException(409, "SEMANTIC_UPLOAD_INCOMPLETE_OR_NOT_STAGING")
            row.state = "FINALIZING"
            audit(session, row, auth["actor"], "semantic_dataset_upload_sealed")
            return upload_receipt(row)

    @app.post(
        "/v1/projects/{project_id}/semantic-cache/dataset-uploads/{upload_id}/cancel",
        response_model=SemanticUploadReceipt,
    )
    def cancel(
        project_id: UUID, upload_id: UUID, body: SemanticUploadSeal, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin", "analyst"))
        with sessions.begin() as session:
            row = owned_upload(session, project_id, upload_id, auth)
            if body.approval_hash != row.approval_hash or row.state == "FINALIZED":
                raise HTTPException(409, "SEMANTIC_UPLOAD_FINALIZED_OR_APPROVAL_MISMATCH")
            row.state = "CANCELLED"
            row.lease_token = row.lease_until = None
            for payload in session.scalars(
                select(PayloadSample)
                .join(
                    SemanticUploadCase,
                    (SemanticUploadCase.payload_id == PayloadSample.id)
                    & (SemanticUploadCase.project_id == PayloadSample.project_id),
                )
                .where(
                    SemanticUploadCase.upload_id == row.id, PayloadSample.project_id == project_id
                )
            ):
                payload.expires_at = min(payload.expires_at, datetime.now(UTC))
            audit(session, row, auth["actor"], "semantic_dataset_upload_cancelled")
            return upload_receipt(row)
