"""Opt-in encrypted experiment records with shared policy quotas and audited reads."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select

from cutback.evals.datasets import digest
from cutback.server.db import (
    AuditEvent,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    PayloadSample,
)


def recorder(sessions, store, project_id, run_id, policy_id):
    def record(value):
        from cutback.server.evaluation_api import active_policy, policy_object_count

        ref = None
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project_id, UUID(policy_id))
                used = policy_object_count(session, UUID(policy_id))
                if used >= policy.max_samples:
                    raise ValueError("CASE_EVIDENCE_POLICY_QUOTA_EXCEEDED")
                blob = json.dumps(value, ensure_ascii=True).encode()
                if len(blob) > policy.max_payload_bytes:
                    raise ValueError("CASE_EVIDENCE_POLICY_BYTES_EXCEEDED")
                ref = store.put(project_id, blob, policy.retention_days)
                session.add(
                    PayloadSample(
                        project_id=project_id,
                        policy_id=UUID(policy_id),
                        object_ref=ref,
                        content_hash=digest(value),
                        expires_at=min(
                            policy.expires_at,
                            datetime.now(UTC) + timedelta(days=policy.retention_days),
                        ),
                        metadata_safe={
                            "kind": "experiment_case",
                            "run_id": str(run_id),
                            "model_key": value["model_key"],
                            "case_hash": digest(value["case"]["id"]),
                        },
                    )
                )
        except BaseException:
            if ref:
                store.delete(project_id, ref)
            raise

    return record


def install_case_evidence(app, sessions, authorize, scope):
    from cutback.server.evaluation_api import ContentPurpose, active_policy, require_store

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cases/content")
    def content(
        project_id: UUID,
        run_id: UUID,
        body: ContentPurpose,
        limit: int = Query(25, ge=1, le=50),
        offset: int = Query(0, ge=0, le=10000),
        auth=Depends(authorize),  # noqa: B008 - FastAPI declarative dependency
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            run = session.get(ExperimentRunRecord, run_id)
            if not run or run.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            plan = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(plan.specification["policy_id"]))
            rows = session.scalars(
                select(PayloadSample)
                .where(
                    PayloadSample.project_id == project_id,
                    PayloadSample.metadata_safe["kind"].as_string() == "experiment_case",
                    PayloadSample.metadata_safe["run_id"].as_string() == str(run_id),
                    PayloadSample.expires_at > datetime.now(UTC),
                    PayloadSample.metadata_safe["deleted"].as_boolean().is_not(True),
                )
                .order_by(
                    PayloadSample.metadata_safe["case_hash"].as_string(),
                    PayloadSample.metadata_safe["model_key"].as_string(),
                    PayloadSample.id,
                )
                .offset(offset)
                .limit(limit)
            ).all()
            values = []
            for row in rows:
                try:
                    value = json.loads(store.get(project_id, row.object_ref))
                except (FileNotFoundError, ValueError):
                    raise HTTPException(410, "CASE_EVIDENCE_EXPIRED_OR_UNAVAILABLE") from None
                if digest(value) != row.content_hash:
                    raise HTTPException(410, "CASE_EVIDENCE_INTEGRITY_FAILED")
                values.append(value)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_case_content_read",
                    details={"run_id": str(run_id), "purpose": body.purpose, "count": len(values)},
                )
            )
        return {
            "cases": values,
            "offset": offset,
            "limit": limit,
            "more_possible": len(values) == limit,
            "content_scope": "explicitly_authorized_encrypted_outputs",
        }
