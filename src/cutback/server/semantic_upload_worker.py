"""Bounded deterministic finalization; retries never invoke an inference provider."""

import re
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import or_, select, true

from cutback.server.dataset_lineage import try_lock_lineage
from cutback.server.db import AuditEvent, PayloadSample
from cutback.server.evaluation_api import persist_dataset
from cutback.server.semantic_upload_db import SemanticDatasetUpload
from cutback.server.semantic_upload_service import staged_cases, upload_authority


def _claim(sessions, identifier):
    now = datetime.now(UTC)
    with sessions() as session:
        candidates = session.execute(
            select(SemanticDatasetUpload.id, SemanticDatasetUpload.project_id)
            .where(
                SemanticDatasetUpload.state == "FINALIZING",
                SemanticDatasetUpload.id == identifier if identifier else true(),
                or_(
                    SemanticDatasetUpload.lease_until.is_(None),
                    SemanticDatasetUpload.lease_until <= now,
                ),
            )
            .order_by(SemanticDatasetUpload.created_at, SemanticDatasetUpload.id)
            .limit(20)
        ).all()
    for identity, project in candidates:
        with sessions.begin() as session:
            if not try_lock_lineage(session, project):
                continue
            row = session.get(SemanticDatasetUpload, identity, with_for_update=True)
            if not row or row.state != "FINALIZING" or row.lease_until and row.lease_until > now:
                continue
            if row.lease_token:
                row.uncertain_attempts += 1
                row.lease_token = row.lease_until = None
            maximum = row.specification["upload"]["max_finalize_attempts"]
            if row.expires_at <= now or row.attempts >= maximum:
                row.state = "EXPIRED" if row.expires_at <= now else "FAILED"
                row.failure_code = "SEMANTIC_UPLOAD_EXPIRED_OR_ATTEMPT_BOUND"
                continue
            row.attempts += 1
            row.lease_token = uuid4()
            seconds = row.specification["upload"]["max_finalize_seconds"]
            row.lease_until = now + timedelta(seconds=seconds + 30)
            row._deadline = monotonic() + seconds
            return row
    return None


def run_semantic_upload_one(sessions, store, *, execution_enabled=False, only_upload_id=None):
    if not execution_enabled:
        return {"status": "SEMANTIC_UPLOAD_FINALIZATION_DISABLED", "billable_inference_calls": 0}
    identifier = UUID(str(only_upload_id)) if only_upload_id else None
    claimed = _claim(sessions, identifier)
    if not claimed:
        return {"status": "NO_ELIGIBLE_SEMANTIC_UPLOAD", "billable_inference_calls": 0}
    try:
        # Dataset assignment, reused-payload metadata, and FINALIZED receipt commit
        # atomically. A lost commit acknowledgement can never create a second dataset.
        with sessions.begin() as session:
            if not try_lock_lineage(session, claimed.project_id):
                raise ValueError("SEMANTIC_UPLOAD_PROJECT_BUSY")
            row = session.get(
                SemanticDatasetUpload, claimed.id, with_for_update=True, populate_existing=True
            )

            def check():
                if (
                    not row
                    or row.state != "FINALIZING"
                    or row.lease_token != claimed.lease_token
                    or not row.lease_until
                    or row.lease_until <= datetime.now(UTC)
                    or monotonic() >= claimed._deadline
                ):
                    raise ValueError("SEMANTIC_UPLOAD_FINALIZATION_LEASE_OR_TIME_DENIED")
                upload_authority(session, row)

            check()
            ns, _, prepared = upload_authority(session, row)
            cases, payloads = staged_cases(
                session, store, row, deadline=claimed._deadline, check=check
            )
            check()
            result = persist_dataset(
                session,
                store,
                row.project_id,
                prepared.metadata.with_cases(cases),
                row.created_by,
                [],
                preexisting_payloads=payloads,
                check=check,
                provenance_extra={"semantic_upload_hash": row.approval_hash},
            )
            for payload in session.scalars(
                select(PayloadSample).where(
                    PayloadSample.project_id == row.project_id,
                    PayloadSample.id.in_(payloads.values()),
                )
            ):
                payload.expires_at = min(payload.expires_at, ns.expires_at)
            check()
            row.dataset_id = UUID(result["id"])
            row.state = "FINALIZED"
            row.lease_token = row.lease_until = None
            session.add(
                AuditEvent(
                    project_id=row.project_id,
                    actor="semantic_upload_worker",
                    action="semantic_dataset_upload_finalized",
                    details={
                        "upload_id": str(row.id),
                        "dataset_id": result["id"],
                        "cases": len(cases),
                        "billable_inference_calls": 0,
                    },
                )
            )
        return {
            "status": "FINALIZED",
            "upload_id": str(claimed.id),
            "dataset_id": result["id"],
            "billable_inference_calls": 0,
            "data_plane_cost_usd": None,
        }
    except Exception as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z0-9_]{1,120}", code):
            code = "SEMANTIC_UPLOAD_FINALIZATION_FAILED"
        with sessions.begin() as session:
            row = session.get(
                SemanticDatasetUpload, claimed.id, with_for_update=True, populate_existing=True
            )
            if row and row.state == "FINALIZED":
                return {
                    "status": "FINALIZED",
                    "upload_id": str(row.id),
                    "dataset_id": str(row.dataset_id),
                    "billable_inference_calls": 0,
                    "data_plane_cost_usd": None,
                }
            if row and row.lease_token == claimed.lease_token:
                row.state, row.failure_code = "FAILED", code
                row.lease_token = row.lease_until = None
        return {
            "status": "FAILED",
            "upload_id": str(claimed.id),
            "code": code,
            "billable_inference_calls": 0,
        }
