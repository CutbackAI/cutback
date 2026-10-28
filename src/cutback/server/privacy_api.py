"""Audited project deletion/export. Delete encrypted objects before relational pointers."""

import json
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select

from cutback.server.db import (
    AccessKey,
    AuditEvent,
    CapturePolicyRecord,
    DailyUsage,
    DatasetRecord,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
    Project,
    ScanRun,
    StoredEvent,
)
from cutback.server.enterprise_db import (
    CacheEntry,
    ExperimentSchedule,
    ProjectSettings,
    RolloutRecord,
    TrainingJob,
)
from cutback.server.lineage_db import DatasetAlias, DatasetExposure, DatasetFamily
from cutback.server.prompt_db import (
    PromptAlias,
    PromptAliasChange,
    PromptDependency,
    PromptExperimentUse,
    PromptObservation,
    PromptReadGrant,
    PromptRecord,
    PromptVersion,
)
from cutback.server.review_db import (
    ReviewItem,
    ReviewLease,
    ReviewQueue,
    ReviewResolution,
    ReviewVote,
)
from cutback.server.trace_db import (
    ExecutionSession,
    ExecutionSpan,
    ExecutionTrace,
    ExternalEvaluation,
    SessionTrace,
    TraceContent,
)


def install_privacy(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/export")
    def export(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="metadata_exported",
                    details={"raw_payloads_included": False},
                )
            )

        def records():
            with sessions() as session:
                for cls in (
                    ExecutionTrace,
                    ExecutionSpan,
                    ExecutionSession,
                    SessionTrace,
                    ExternalEvaluation,
                    TraceContent,
                    PayloadSample,
                    DatasetFamily,
                    DatasetAlias,
                    DatasetExposure,
                    PromptRecord,
                    PromptVersion,
                    PromptDependency,
                    PromptAlias,
                    PromptAliasChange,
                    PromptReadGrant,
                    PromptObservation,
                    PromptExperimentUse,
                    ReviewQueue,
                    ReviewItem,
                    ReviewLease,
                    ReviewVote,
                    ReviewResolution,
                    ProjectSettings,
                    ExperimentSchedule,
                    RolloutRecord,
                    TrainingJob,
                    CacheEntry,
                    StoredEvent,
                    DailyUsage,
                    ScanRun,
                    DatasetRecord,
                    ExperimentPlanRecord,
                    ExperimentRunRecord,
                    CapturePolicyRecord,
                    AuditEvent,
                ):
                    for row in session.scalars(
                        select(cls)
                        .where(cls.project_id == project_id)
                        .execution_options(yield_per=500)
                    ):
                        data = {
                            column.name: getattr(row, column.name)
                            for column in cls.__table__.columns
                            if cls is not PayloadSample or column.name != "object_ref"
                        }
                        yield (
                            json.dumps({"table": cls.__tablename__, "record": data}, default=str)
                            + "\n"
                        )

        return StreamingResponse(
            records(),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": 'attachment; filename="cutback-metadata.ndjson"'},
        )

    @app.delete("/v1/projects/{project_id}")
    def delete_project(project_id: UUID, confirm_slug: str, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            project = session.get(Project, project_id, with_for_update=True)
            if not project or confirm_slug != project.slug:
                raise HTTPException(409, "PROJECT_DELETE_REQUIRES_EXACT_SLUG")
            running = session.scalar(
                select(JobRecord)
                .where(
                    JobRecord.project_id == project_id, JobRecord.status.in_(["QUEUED", "RUNNING"])
                )
                .limit(1)
            )
            training_running = session.scalar(
                select(TrainingJob)
                .where(
                    TrainingJob.project_id == project_id,
                    TrainingJob.status.in_(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]),
                )
                .limit(1)
            )
            if training_running:
                raise HTTPException(409, "CANCEL_OR_FINISH_RUNNING_TRAINING_FIRST")
            if running:
                raise HTTPException(409, "CANCEL_OR_FINISH_RUNNING_EXPERIMENTS_FIRST")
            for policy in session.scalars(
                select(CapturePolicyRecord)
                .where(CapturePolicyRecord.project_id == project_id)
                .with_for_update()
            ):
                policy.enabled = False
            payloads = session.scalars(
                select(PayloadSample)
                .where(PayloadSample.project_id == project_id)
                .with_for_update()
            ).all()
            plans = session.scalars(
                select(ExperimentPlanRecord).where(ExperimentPlanRecord.project_id == project_id)
            ).all()
            caches = session.scalars(
                select(CacheEntry).where(CacheEntry.project_id == project_id)
            ).all()
            trained = session.scalars(
                select(TrainingJob).where(TrainingJob.project_id == project_id)
            ).all()
            if payloads or plans or caches or any(job.results.get("artifacts") for job in trained):
                from cutback.evals.artifacts import configured_store

                store = getattr(app.state, "artifact_store", None) or configured_store()
                for job in trained:
                    for artifact in job.results.get("artifacts", []):
                        for chunk in artifact["chunks"]:
                            store.delete(project_id, chunk["ref"])
                for payload in payloads:
                    store.delete(project_id, payload.object_ref)
                for plan in plans:
                    if plan.specification.get("artifact_ref"):
                        store.delete(project_id, plan.specification["artifact_ref"])
                for cache in caches:
                    store.delete(project_id, cache.object_ref)
            # Deletion order follows restrictive FKs. Project cascades remove the remaining records.
            from cutback.server.db import ChangeProposal, DatasetItem, RecommendationRecord

            for cls in (
                PromptAliasChange,
                PromptAlias,
                PromptDependency,
                PromptObservation,
                PromptExperimentUse,
                PromptReadGrant,
                PromptVersion,
                PromptRecord,
                ReviewResolution,
                ReviewVote,
                ReviewLease,
                ReviewItem,
                ReviewQueue,
                CacheEntry,
                TrainingJob,
                ExperimentSchedule,
                RolloutRecord,
                ChangeProposal,
                RecommendationRecord,
                JobRecord,
                ExperimentRunRecord,
                ExperimentPlanRecord,
            ):
                session.execute(delete(cls).where(cls.project_id == project_id))
            dataset_ids = select(DatasetRecord.id).where(DatasetRecord.project_id == project_id)
            session.execute(delete(DatasetItem).where(DatasetItem.dataset_id.in_(dataset_ids)))
            session.execute(delete(DatasetRecord).where(DatasetRecord.project_id == project_id))
            session.execute(delete(PayloadSample).where(PayloadSample.project_id == project_id))
            session.execute(delete(AccessKey).where(AccessKey.project_id == project_id))
            session.execute(delete(AuditEvent).where(AuditEvent.project_id == project_id))
            session.delete(project)
            # Keep only a nonsensitive installation-wide deletion receipt, without project data.
            session.add(
                AuditEvent(
                    project_id=None,
                    actor=auth["actor"],
                    action="project_deleted",
                    details={"project_id": str(project_id), "backup_deletion": "operator_policy"},
                )
            )
        return {
            "status": "deleted",
            "project_id": str(project_id),
            "backups": "follow customer backup expiry and legal-hold policy",
        }
