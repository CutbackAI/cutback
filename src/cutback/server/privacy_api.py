"""Audited project deletion/export. Delete encrypted objects before relational pointers."""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select, update

from cutback.server.campaign_db import (
    CampaignCandidate,
    CampaignEconomicReference,
    CampaignRecord,
    CampaignRun,
    CampaignSelection,
)
from cutback.server.criteria_db import CriteriaExperimentUse, CriteriaVersionRecord
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
from cutback.server.economics_db import (
    ImprovementObservation,
    TaskCostSnapshot,
    TaskRecord,
    TaskResult,
)
from cutback.server.enterprise_db import (
    CacheEntry,
    ExperimentSchedule,
    ProjectSettings,
    RolloutRecord,
    TrainingJob,
)
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.gateway_db import (
    GatewayBudgetRow,
    GatewayCallRow,
    GatewayKeyRow,
    GatewayPolicyRow,
)
from cutback.server.guardrail_db import GuardrailOperationRow, GuardrailPolicyRow
from cutback.server.lineage_db import DatasetAlias, DatasetExposure, DatasetFamily
from cutback.server.monitoring_db import (
    AlertRule,
    AlertWindow,
    Incident,
    IncidentEvent,
    NotificationConfig,
    NotificationDelivery,
    OnlineEvaluation,
    OnlineIntake,
    OnlineRule,
    SavedView,
)
from cutback.server.native_profile_db import NativeProfileAnalysis
from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow
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
from cutback.server.provider_batch_db import ProviderBatchRecord
from cutback.server.quality_db import QualityEvaluation, QualityPromptUse, QualityRule
from cutback.server.review_db import (
    ReviewItem,
    ReviewLease,
    ReviewQueue,
    ReviewResolution,
    ReviewVote,
)
from cutback.server.scenario_db import ScenarioDatasetImport, ScenarioVersionRecord
from cutback.server.semantic_cache_db import (
    SemanticCacheMember,
    SemanticCacheNamespace,
    SemanticCacheQualification,
)
from cutback.server.semantic_upload_db import SemanticDatasetUpload, SemanticUploadCase
from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord
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
                    ScenarioDatasetImport,
                    ScenarioVersionRecord,
                    CriteriaExperimentUse,
                    CriteriaVersionRecord,
                    GuardrailPolicyRow,
                    GuardrailOperationRow,
                    SemanticCacheNamespace,
                    SemanticCacheMember,
                    SemanticCacheQualification,
                    SemanticDatasetUpload,
                    SemanticUploadCase,
                    ProviderBatchRecord,
                    GatewayPolicyRow,
                    GatewayKeyRow,
                    GatewayBudgetRow,
                    GatewayCallRow,
                    OutcomeWatch,
                    OutcomeWatchWindow,
                    CampaignRecord,
                    CampaignRun,
                    CampaignCandidate,
                    CampaignSelection,
                    CampaignEconomicReference,
                    TaskRecord,
                    TaskResult,
                    TaskCostSnapshot,
                    ImprovementObservation,
                    ExecutionCall,
                    ExecutionEpisode,
                    ToolSessionRecord,
                    ToolOperationRecord,
                    NotificationConfig,
                    NotificationDelivery,
                    QualityRule,
                    QualityEvaluation,
                    QualityPromptUse,
                    OnlineRule,
                    OnlineIntake,
                    OnlineEvaluation,
                    AlertRule,
                    AlertWindow,
                    Incident,
                    IncidentEvent,
                    SavedView,
                    ExecutionTrace,
                    ExecutionSpan,
                    ExecutionSession,
                    SessionTrace,
                    ExternalEvaluation,
                    TraceContent,
                    PayloadSample,
                    NativeProfileAnalysis,
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
                            if cls is not PayloadSample
                            or column.name not in {"object_ref", "native_profile_ref"}
                            if cls is not GatewayKeyRow or column.name != "digest"
                            if cls is not GatewayCallRow or column.name != "request_hmac"
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
            from cutback.server.dataset_lineage import lock_lineage

            lock_lineage(session, project_id)
            project = session.get(Project, project_id, with_for_update=True)
            if not project or confirm_slug != project.slug:
                raise HTTPException(409, "PROJECT_DELETE_REQUIRES_EXACT_SLUG")
            batches = session.scalars(
                select(ProviderBatchRecord).where(ProviderBatchRecord.project_id == project_id)
            ).all()
            if any(
                row.pieces and any(part.get("cleanup") != "deleted" for part in row.pieces)
                for row in batches
            ):
                raise HTTPException(
                    409, "RECONCILE_AND_CLEAN_PROVIDER_BATCH_FILES_BEFORE_PROJECT_DELETE"
                )
            if session.scalar(
                select(GatewayCallRow.operation_id)
                .where(
                    GatewayCallRow.project_id == project_id,
                    GatewayCallRow.state == "DISPATCHED",
                    GatewayCallRow.lease_until > datetime.now(UTC),
                )
                .limit(1)
            ):
                raise HTTPException(409, "FINISH_IN_FLIGHT_GATEWAY_CALL_FIRST")
            if session.scalar(
                select(NotificationDelivery.id)
                .where(
                    NotificationDelivery.project_id == project_id,
                    NotificationDelivery.state == "leased",
                    NotificationDelivery.lease_until > datetime.now(UTC),
                )
                .limit(1)
            ):
                raise HTTPException(409, "FINISH_IN_FLIGHT_NOTIFICATION_FIRST")
            if session.scalar(
                select(ToolSessionRecord.id)
                .where(
                    ToolSessionRecord.project_id == project_id,
                    ToolSessionRecord.cleanup_confirmed.is_(False),
                )
                .limit(1)
            ):
                raise HTTPException(
                    409, "CONFIRM_ISOLATED_TOOL_CONTAINMENT_BEFORE_PROJECT_DELETION"
                )
            if session.scalar(
                select(ExecutionCall.id)
                .where(
                    ExecutionCall.project_id == project_id,
                    ExecutionCall.state == "reserved",
                    ExecutionCall.lease_until > datetime.now(UTC),
                )
                .limit(1)
            ):
                raise HTTPException(409, "FINISH_IN_FLIGHT_EXECUTION_CALL_FIRST")
            if session.scalar(
                select(QualityEvaluation.id)
                .where(
                    QualityEvaluation.project_id == project_id,
                    QualityEvaluation.state == "leased",
                    QualityEvaluation.lease_until > datetime.now(UTC),
                )
                .limit(1)
            ):
                raise HTTPException(409, "FINISH_IN_FLIGHT_QUALITY_EVALUATION_FIRST")
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
                    if payload.native_profile_ref is not None:
                        store.delete(project_id, payload.native_profile_ref)
                for plan in plans:
                    if plan.specification.get("artifact_ref"):
                        store.delete(project_id, plan.specification["artifact_ref"])
                for cache in caches:
                    store.delete(project_id, cache.object_ref)
            # Deletion order follows restrictive FKs. Project cascades remove the remaining records.
            from cutback.server.db import ChangeProposal, DatasetItem, RecommendationRecord

            session.execute(
                update(SemanticCacheNamespace)
                .where(SemanticCacheNamespace.project_id == project_id)
                .values(state="REVOKED", active_qualification_id=None, active_approval_hash=None)
            )
            for cls in (
                ScenarioDatasetImport,
                ScenarioVersionRecord,
                CriteriaExperimentUse,
                CriteriaVersionRecord,
                SemanticUploadCase,
                SemanticDatasetUpload,
                SemanticCacheMember,
                SemanticCacheQualification,
                SemanticCacheNamespace,
                ProviderBatchRecord,
                GatewayCallRow,
                GatewayKeyRow,
                GatewayBudgetRow,
                GatewayPolicyRow,
                OutcomeWatchWindow,
                OutcomeWatch,
                CampaignSelection,
                CampaignCandidate,
                CampaignRun,
                CampaignEconomicReference,
                CampaignRecord,
                ImprovementObservation,
                TaskResult,
                TaskRecord,
                TaskCostSnapshot,
                ExecutionCall,
                ToolOperationRecord,
                ToolSessionRecord,
                GuardrailOperationRow,
                GuardrailPolicyRow,
                ExecutionEpisode,
                QualityEvaluation,
                QualityPromptUse,
                QualityRule,
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
