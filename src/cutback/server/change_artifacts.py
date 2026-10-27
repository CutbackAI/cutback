"""Explicit, encrypted upload of locally reviewed source changes. Never applies a patch."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.server.content_policy import policy_content
from cutback.server.db import (
    AuditEvent,
    ChangeProposal,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
    RecommendationRecord,
    ScanRun,
)
from cutback.server.evaluation_api import (
    ContentPurpose,
    active_policy,
    policy_object_count,
    require_store,
)


class PatchUpload(StrictModel):
    approval_ref: Identifier
    artifact: dict = Field(max_length=30)


def install_changes(app, sessions, authorize, scope):
    @app.post(
        "/v1/projects/{project_id}/recommendations/{recommendation_id}/patches", status_code=201
    )
    def upload(
        project_id: UUID, recommendation_id: UUID, body: PatchUpload, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        store, ref = require_store(app), None
        try:
            with sessions.begin() as session:
                rec = session.get(RecommendationRecord, recommendation_id)
                if not rec or rec.project_id != project_id:
                    raise HTTPException(404, "RECOMMENDATION_NOT_FOUND")
                if (
                    rec.status != "accepted_for_staging"
                    or not rec.evidence.get("quality_gate_passed")
                    or not rec.evidence.get("cheaper_on_sample")
                ):
                    raise HTTPException(
                        409, "RECOMMENDATION_REQUIRES_COST_QUALITY_AND_HUMAN_REVIEW"
                    )
                run = session.get(ExperimentRunRecord, rec.run_id)
                plan = session.get(ExperimentPlanRecord, run.plan_id)
                _, policy = active_policy(
                    session, project_id, UUID(plan.specification["policy_id"])
                )
                artifact = body.artifact
                expected = {
                    "recommendation_id": str(rec.id),
                    "run_id": str(run.id),
                    "dataset_hash": rec.evidence["dataset_hash"],
                    "plan_hash": rec.evidence["plan_hash"],
                    "production_deployed": False,
                    "status": "DRY_RUN_ONLY",
                }
                if any(artifact.get(key) != value for key, value in expected.items()):
                    raise HTTPException(422, "PATCH_EVIDENCE_BINDING_MISMATCH")
                path = PurePosixPath(artifact.get("source_file", ""))
                if (
                    not path.parts
                    or path.is_absolute()
                    or any(
                        part in {"..", "."} or ":" in part or "\\" in part for part in path.parts
                    )
                ):
                    raise HTTPException(422, "PATCH_SOURCE_PATH_INVALID")
                if not all(
                    isinstance(artifact.get(key), str) and len(artifact[key]) <= 200000
                    for key in ("diff", "rollback_diff", "source_sha256", "call_site_id")
                ):
                    raise HTTPException(422, "PATCH_CONTENT_INVALID")
                if not artifact["diff"] or hashlib.sha256(
                    artifact["diff"].encode()
                ).hexdigest() != artifact.get("diff_sha256"):
                    raise HTTPException(422, "PATCH_DIGEST_MISMATCH")
                if not re.fullmatch(r"[a-f0-9]{64}", artifact["source_sha256"]):
                    raise HTTPException(422, "PATCH_SOURCE_HASH_INVALID")
                scans = session.scalars(
                    select(ScanRun)
                    .where(
                        ScanRun.project_id == project_id,
                        ScanRun.commit_sha == artifact.get("source_commit"),
                    )
                    .order_by(ScanRun.created_at.desc())
                    .limit(100)
                ).all()
                if not any(
                    site["call_site_id"] == artifact["call_site_id"]
                    and site["source_file"] == str(path)
                    for scan in scans
                    for site in scan.inventory["call_sites"]
                ):
                    raise HTTPException(409, "PATCH_SOURCE_SCAN_REQUIRED")
                try:
                    clean, blob = policy_content(artifact, policy, preserve_semantics=True)
                except ValueError:
                    raise HTTPException(422, "PATCH_CONTENT_POLICY_DENIED") from None
                if (
                    policy_object_count(session, UUID(plan.specification["policy_id"]))
                    >= policy.max_samples
                ):
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                ref = store.put(project_id, blob, policy.retention_days)
                payload = PayloadSample(
                    project_id=project_id,
                    policy_id=UUID(plan.specification["policy_id"]),
                    object_ref=ref,
                    content_hash=digest(clean),
                    expires_at=min(
                        policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
                    ),
                    metadata_safe={"kind": "reviewed_patch", "recommendation_id": str(rec.id)},
                )
                session.add(payload)
                session.flush()
                change = ChangeProposal(
                    project_id=project_id,
                    recommendation_id=rec.id,
                    status="review_required",
                    evidence={
                        "payload_id": str(payload.id),
                        "diff_sha256": artifact["diff_sha256"],
                        "run_id": str(run.id),
                        "approval_ref": body.approval_ref,
                        "expires_at": payload.expires_at.isoformat(),
                        "source_verified_by": "local_cli_attestation",
                    },
                )
                session.add(change)
                session.flush()
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor=auth["actor"],
                        action="reviewed_patch_uploaded",
                        details={
                            "change_id": str(change.id),
                            "approval_ref": body.approval_ref,
                            "diff_sha256": artifact["diff_sha256"],
                        },
                    )
                )
                result = {
                    "id": str(change.id),
                    "status": change.status,
                    "production_deployed": False,
                }
            return result
        except BaseException:
            if ref:
                store.delete(project_id, ref)
            raise

    @app.get("/v1/projects/{project_id}/changes")
    def list_changes(
        project_id: UUID, offset: int = Query(0, ge=0, le=10000), auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin", "editor", "viewer"))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "recommendation_id": str(row.recommendation_id),
                    "status": row.status,
                    "evidence": row.evidence,
                }
                for row in session.scalars(
                    select(ChangeProposal)
                    .where(ChangeProposal.project_id == project_id)
                    .order_by(ChangeProposal.id)
                    .offset(offset)
                    .limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/changes/{change_id}/content")
    def content(project_id: UUID, change_id: UUID, body: ContentPurpose, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            change = session.get(ChangeProposal, change_id)
            if not change or change.project_id != project_id:
                raise HTTPException(404, "CHANGE_NOT_FOUND")
            payload = session.get(PayloadSample, UUID(change.evidence["payload_id"]))
            if (
                not payload
                or payload.project_id != project_id
                or payload.expires_at <= datetime.now(UTC)
                or payload.metadata_safe.get("deleted")
            ):
                raise HTTPException(410, "PATCH_EXPIRED_OR_UNAVAILABLE")
            _, policy = active_policy(session, project_id, payload.policy_id)
            try:
                value = json.loads(store.get(project_id, payload.object_ref))
                if digest(value) != payload.content_hash:
                    raise ValueError("INTEGRITY_FAILURE")
                policy_content(value, policy, preserve_semantics=True)
            except (OSError, ValueError, InvalidTag):
                raise HTTPException(410, "PATCH_EXPIRED_OR_POLICY_CHANGED") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="reviewed_patch_content_read",
                    details={"change_id": str(change_id), "purpose": body.purpose},
                )
            )
        return value
