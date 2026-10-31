"""Control-plane dataset authorization, immutable plans and audited human decisions."""

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import UUID4, Field
from sqlalchemy import func, select

from cutback.core.episode_judging import EpisodeJudgingSpec
from cutback.core.isolated_episodes import (
    GuardedRecordExecution,
    IsolatedRecordExecution,
    IsolatedSecurityExecution,
)
from cutback.core.models import Identifier, StrictModel
from cutback.core.retrieval import RetrievalConfigurationBinding, RetrievalCorpus, RetrievalSettings
from cutback.core.security_suites import SecurityEvaluationSpec, family_token
from cutback.evals.artifacts import configured_store
from cutback.evals.datasets import (
    CapturePolicy,
    Case,
    DatasetImport,
    Sample,
    digest,
    prepare_dataset,
    redact,
)
from cutback.evals.experiments import (
    Budget,
    CriterionSpec,
    ExperimentPlan,
    ModelSpec,
    UserSimulationInput,
    plan_summary,
)
from cutback.evals.metrics import MetricSpec
from cutback.evals.retrieval import AssumedCPURate
from cutback.retrieval_changes import RetrievalProposal
from cutback.server.db import (
    AuditEvent,
    CapturePolicyRecord,
    DatasetItem,
    DatasetRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
    RecommendationRecord,
    ScanRun,
)
from cutback.server.plan_artifacts import freeze_plan, load_plan, target_evaluation_clause


def policy_object_count(session, policy_id):
    from cutback.server.criteria_db import CriteriaVersionRecord
    from cutback.server.enterprise_db import CacheEntry
    from cutback.server.quality_db import QualityEvaluation, QualityRule
    from cutback.server.scenario_db import ScenarioVersionRecord
    from cutback.server.semantic_upload_db import SemanticDatasetUpload

    stored = sum(
        session.scalar(select(func.count()).select_from(cls).where(cls.policy_id == policy_id)) or 0
        for cls in (PayloadSample, CacheEntry)
    )
    reserved = (
        session.scalar(
            select(func.count())
            .select_from(QualityEvaluation)
            .join(QualityRule)
            .where(
                QualityRule.specification["source_policy_id"].as_string() == str(policy_id),
                QualityEvaluation.output_reserved.is_(True),
                QualityEvaluation.lease_until > datetime.now(UTC),
            )
        )
        or 0
    )
    uploads = (
        session.scalar(
            select(
                func.sum(
                    SemanticDatasetUpload.expected_cases - SemanticDatasetUpload.received_cases
                )
            ).where(
                SemanticDatasetUpload.policy_id == policy_id,
                SemanticDatasetUpload.state == "STAGING",
                SemanticDatasetUpload.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    criteria_outputs = (
        session.scalar(
            select(func.count())
            .select_from(CriteriaVersionRecord)
            .where(
                CriteriaVersionRecord.capture_policy_id == policy_id,
                CriteriaVersionRecord.output_payload_id.is_(None),
                CriteriaVersionRecord.state.in_(["PENDING", "QUEUED", "RUNNING"]),
                CriteriaVersionRecord.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    scenario_outputs = (
        session.scalar(
            select(func.count())
            .select_from(ScenarioVersionRecord)
            .where(
                ScenarioVersionRecord.capture_policy_id == policy_id,
                ScenarioVersionRecord.output_payload_id.is_(None),
                ScenarioVersionRecord.state.in_(["PENDING", "QUEUED", "RUNNING"]),
                ScenarioVersionRecord.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    return stored + reserved + uploads + criteria_outputs + scenario_outputs


class PlanInput(StrictModel):
    request_id: UUID4 | None = None
    dataset_id: UUID
    specification: ExperimentPlan


class ApproveInput(StrictModel):
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class QueueInput(StrictModel):
    plan_id: UUID


class RetrievalDraftInput(StrictModel):
    dataset_id: UUID
    baseline: ModelSpec
    corpus: RetrievalCorpus
    settings: RetrievalSettings
    metric: MetricSpec
    budget: Budget
    candidates: list[RetrievalSettings] | None = Field(default=None, min_length=1, max_length=5)
    assumed_usd_per_cpu_hour: AssumedCPURate | None = None
    max_query_bytes: int = Field(default=1024, strict=True, ge=1, le=8192)
    retain_case_outputs: bool = False
    configuration_binding: RetrievalConfigurationBinding | None = None


class RetrievalConfirmationInput(StrictModel):
    validation_run_id: UUID
    budget: Budget


class DraftInput(StrictModel):
    user_simulation: UserSimulationInput | None = None
    isolated_tools: (
        IsolatedRecordExecution | IsolatedSecurityExecution | GuardedRecordExecution | None
    ) = None
    security_suite: SecurityEvaluationSpec | None = None
    episode_judging: EpisodeJudgingSpec | None = None
    episode_judge: ModelSpec | None = None
    episode_max_turns: int = Field(default=32, ge=1, le=32)
    name: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9 .:/_-]*$"
    )
    criteria: list[CriterionSpec] = Field(default_factory=list, max_length=8)
    retain_case_outputs: bool = False
    dataset_id: UUID
    baseline: ModelSpec
    candidates: list[ModelSpec] | None = Field(default=None, min_length=1, max_length=5)
    minimum: float | None = Field(default=None, ge=0, le=1)
    max_usd: str = Field(default="0", max_length=30)
    max_requests: int = Field(default=100, ge=1, le=10000)
    repeats: int = Field(default=1, ge=1, le=5)
    pilot_samples: int = Field(default=0, ge=0, le=100)
    pilot_minimum: float | None = Field(default=None, ge=0, le=1)
    max_wall_seconds: int = Field(default=300, ge=1, le=3600)


class ReviewInput(StrictModel):
    decision: Literal["accepted_for_staging", "rejected"]
    reason: Identifier


class ContentPurpose(StrictModel):
    purpose: Identifier


class LineageReconciliation(ContentPurpose):
    immutable_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def require_store(app):
    if os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower() != "true":
        raise HTTPException(403, "PAYLOAD_STORAGE_DISABLED")
    try:
        return getattr(app.state, "artifact_store", None) or configured_store()
    except (ValueError, ImportError, OSError):
        raise HTTPException(503, "PAYLOAD_STORAGE_NOT_CONFIGURED") from None


def active_policy(session, project, policy_id):
    row = session.get(CapturePolicyRecord, policy_id, with_for_update=True, populate_existing=True)
    if not row or row.project_id != project or not row.enabled:
        raise HTTPException(403, "CAPTURE_POLICY_DISABLED_OR_OUT_OF_SCOPE")
    policy = CapturePolicy.model_validate(row.policy)
    if policy.expires_at <= datetime.now(UTC):
        raise HTTPException(403, "CAPTURE_POLICY_EXPIRED")
    return row, policy


def dataset_manifest(session, dataset):
    rows = session.execute(
        select(DatasetItem, PayloadSample)
        .join(PayloadSample, DatasetItem.payload_id == PayloadSample.id)
        .where(DatasetItem.dataset_id == dataset.id)
        .order_by(PayloadSample.metadata_safe["ordinal"].as_integer(), DatasetItem.id)
    ).all()
    return {
        "immutable_hash": dataset.immutable_hash,
        **(
            {"security_suite": dataset.provenance["security_suite"]}
            if dataset.provenance.get("security_suite")
            else {}
        ),
        "items": [
            {
                "split": row.split,
                "episode_max_turns": payload.metadata_safe.get("episode_max_turns"),
                "episode_max_tool_calls": payload.metadata_safe.get("episode_max_tool_calls"),
                "episode_execution": payload.metadata_safe.get("episode_execution"),
                **(
                    {
                        key: payload.metadata_safe.get(key)
                        for key in ("security_family_token", "security_arm")
                    }
                    if dataset.provenance.get("security_suite")
                    else {}
                ),
            }
            for row, payload in rows
        ],
    }


def persist_dataset(
    session,
    store,
    project_id,
    body,
    actor,
    stored,
    *,
    provenance_extra=None,
    preexisting_payloads=None,
    check=None,
    development_only=False,
    dataset_id=None,
):
    from cutback.server.dataset_lineage import lock_lineage, register_groups

    lock_lineage(session, project_id)
    if body.security_suite:
        existing_suites = session.scalars(
            select(DatasetRecord)
            .where(
                DatasetRecord.project_id == project_id,
                DatasetRecord.provenance["security_suite"]["key"].as_string()
                == body.security_suite.key,
                DatasetRecord.provenance["security_suite"]["revision"].as_string()
                == body.security_suite.revision,
            )
            .limit(1)
        ).all()
        if existing_suites and existing_suites[0].provenance[
            "security_suite"
        ] != body.security_suite.model_dump(mode="json"):
            raise HTTPException(409, "SECURITY_SUITE_REVISION_ALREADY_FROZEN")
    try:
        policy_id = UUID(body.policy_id)
    except ValueError:
        raise HTTPException(422, "INVALID_POLICY_ID") from None
    _, policy = active_policy(session, project_id, policy_id)
    if not policy.permits(body.environment, body.workflow):
        raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
    if body.source_type == "synthetic_fixture" and policy.rights_basis != "synthetic":
        raise HTTPException(422, "SYNTHETIC_PROVENANCE_POLICY_MISMATCH")
    session.get(CapturePolicyRecord, policy_id, with_for_update=True)
    reused = {}
    if preexisting_payloads is not None:
        if set(preexisting_payloads) != {case.id for case in body.cases} or len(
            set(preexisting_payloads.values())
        ) != len(body.cases):
            raise HTTPException(422, "DATASET_REUSABLE_PAYLOAD_IDENTITIES_INVALID")
        rows = session.scalars(
            select(PayloadSample)
            .where(
                PayloadSample.id.in_(preexisting_payloads.values()),
                PayloadSample.project_id == project_id,
                PayloadSample.policy_id == policy_id,
            )
            .with_for_update()
        ).all()
        by_id = {row.id: row for row in rows}
        if len(rows) != len(body.cases) or session.scalar(
            select(DatasetItem.id).where(DatasetItem.payload_id.in_(by_id)).limit(1)
        ):
            raise HTTPException(409, "DATASET_REUSABLE_PAYLOAD_MISSING_OR_ALREADY_ASSIGNED")
        for case in body.cases:
            payload = by_id[preexisting_payloads[case.id]]
            if (
                payload.metadata_safe.get("deleted")
                or payload.expires_at <= datetime.now(UTC)
                or payload.content_hash != digest(case.model_dump(mode="json"))
            ):
                raise HTTPException(409, "DATASET_REUSABLE_PAYLOAD_EXPIRED_OR_CHANGED")
            reused[case.id] = payload
    existing = policy_object_count(session, policy_id)
    if existing + len(body.cases) - len(reused) > policy.max_samples:
        raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
    # Serialize policy consumption across concurrent imports before checking quota.
    clean = DatasetImport.model_validate(
        redact(body.model_dump(mode="json"), policy.never_capture_fields)
    )
    if reused and clean.model_dump(mode="json") != body.model_dump(mode="json"):
        raise HTTPException(422, "DATASET_REUSABLE_CONTENT_REQUIRES_APPLICATION_DEIDENTIFICATION")
    lineage = register_groups(session, project_id, clean.cases, actor=actor)
    prepared = prepare_dataset(
        clean, lineage_groups={key: str(value) for key, value in lineage.items()}
    )
    if development_only:
        for item in prepared["items"]:
            item["split"] = "validation"
        prepared["split_counts"] = {"train": 0, "validation": len(clean.cases), "test": 0}
        prepared["split_strategy"] = "reviewed-generated-development-only-v1"
    provenance = clean.model_dump(mode="json", exclude={"cases"})
    provenance.update(
        {
            "split_counts": prepared["split_counts"],
            "task_inference": prepared["task_inference"],
            "split_strategy": prepared["split_strategy"],
            "group_count": prepared["group_count"],
            "strata": prepared["strata"],
            "warnings": prepared["warnings"],
            "case_count": len(clean.cases),
            "max_input_bytes": max(len(case.input.encode()) for case in clean.cases),
            "redaction": policy.redaction_version,
        }
    )
    provenance.update(provenance_extra or {})
    from cutback.server.dataset_lineage import dataset_version_hash

    assignment_hash = digest(
        [
            [i, item["split"], item["group_hash"], str(lineage[item["case"]["id"]])]
            for i, item in enumerate(prepared["items"])
        ]
    )
    provenance.update(
        {
            "hash_scheme": "cutback.dataset.lineage.v1",
            "content_hash": prepared["immutable_hash"],
            "assignment_hash": assignment_hash,
            "provenance_extension_keys": sorted(provenance_extra or {}),
        }
    )
    dataset = DatasetRecord(
        **({"id": dataset_id} if dataset_id is not None else {}),
        project_id=project_id,
        workflow_id=body.workflow,
        immutable_hash=dataset_version_hash(
            provenance, prepared["immutable_hash"], assignment_hash
        ),
        provenance=provenance,
    )
    session.add(dataset)
    session.flush()
    for ordinal, item in enumerate(prepared["items"]):
        if check:
            check()
        blob = json.dumps(item["case"]).encode()
        if len(blob) > policy.max_payload_bytes:
            raise HTTPException(413, "SAMPLE_EXCEEDS_POLICY_BYTES")
        payload = reused.get(item["case"]["id"])
        if payload is None:
            ref = store.put(project_id, blob, policy.retention_days)
            stored.append(ref)
            payload = PayloadSample(
                project_id=project_id,
                policy_id=policy_id,
                object_ref=ref,
                content_hash=digest(item["case"]),
                expires_at=min(
                    policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
                ),
                metadata_safe={
                    "redaction": policy.redaction_version,
                    "ordinal": ordinal,
                    **(
                        {
                            "security_family_token": family_token(
                                item["case"]["security"]["content_nonce"],
                                item["case"]["security"]["family"],
                            ),
                            "security_arm": item["case"]["security"]["arm"],
                        }
                        if clean.security_suite
                        else {}
                    ),
                    **(
                        {
                            "episode_max_turns": item["case"]["episode"]["max_turns"],
                            "episode_max_tool_calls": item["case"]["episode"]["max_tool_calls"],
                            "episode_execution": item["case"]["episode"]["execution"],
                        }
                        if item["case"].get("episode")
                        else {}
                    ),
                },
            )
        else:
            payload.metadata_safe = {
                **payload.metadata_safe,
                "redaction": policy.redaction_version,
                "ordinal": ordinal,
                "kind": "dataset_case",
            }
            payload.expires_at = min(
                policy.expires_at, datetime.now(UTC) + timedelta(days=policy.retention_days)
            )
        session.add(payload)
        session.flush()
        session.add(
            DatasetItem(
                dataset_id=dataset.id,
                payload_id=payload.id,
                split=item["split"],
                group_hash=item["group_hash"],
                lineage_group_id=lineage[item["case"]["id"]],
            )
        )
    session.add(
        AuditEvent(
            project_id=project_id,
            actor=actor,
            action="dataset_imported",
            details={"dataset_id": str(dataset.id), "cases": len(clean.cases)},
        )
    )
    return {
        "id": str(dataset.id),
        "immutable_hash": dataset.immutable_hash,
        "provenance": provenance,
        "content": "encrypted_separate_store",
    }


def install_evaluation(app, sessions, authorize, scope):
    @app.post("/v1/projects/{project_id}/datasets/{dataset_id}/lineage/reconcile")
    def reconcile_lineage(
        project_id: UUID, dataset_id: UUID, body: LineageReconciliation, auth=Depends(authorize)
    ):
        """Supplement legacy identities without editing their frozen content, splits or hashes."""
        from cutback.server.dataset_lineage import (
            lock_lineage,
            register_groups,
            require_unreserved_groups,
        )
        from cutback.server.lineage_db import DatasetExposure
        from cutback.server.worker import load_dataset

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            dataset = session.get(DatasetRecord, dataset_id, with_for_update=True)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            if dataset.immutable_hash != body.immutable_hash:
                raise HTTPException(409, "DATASET_HASH_CHANGED")
            rows = session.scalars(
                select(DatasetItem)
                .where(DatasetItem.dataset_id == dataset_id)
                .order_by(DatasetItem.id)
            ).all()
            if all(r.lineage_group_id is not None for r in rows):
                return {"status": "already_known", "frozen_dataset_changed": False}
            if dataset.provenance.get("hash_scheme") == "cutback.dataset.lineage.v1":
                raise HTTPException(409, "FROZEN_LINEAGE_ASSIGNMENT_MISSING")
            try:
                loaded, _ = load_dataset(session, dataset, store)
                cases = [Case.model_validate(item["case"]) for item in loaded["items"]]
                families = register_groups(session, project_id, cases, actor=auth["actor"])
                require_unreserved_groups(session, project_id, set(families.values()))
            except (ValueError, OSError):
                raise HTTPException(409, "LEGACY_CONTENT_UNAVAILABLE_OR_GROUP_RESERVED") from None
            for row in rows:
                payload = session.get(PayloadSample, row.payload_id)
                case = cases[payload.metadata_safe["ordinal"]]
                if row.lineage_group_id is not None and row.lineage_group_id != families[case.id]:
                    raise HTTPException(409, "PARTIAL_LEGACY_LINEAGE_CONFLICT")
                row.lineage_group_id = families[case.id]
            for family in set(families.values()):
                session.add(
                    DatasetExposure(
                        project_id=project_id,
                        family_id=family,
                        dataset_id=dataset.id,
                        run_id=None,
                        source_ref="legacy-reconciliation:" + str(dataset.id),
                        mode="validation",
                        protected=False,
                    )
                )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="legacy_dataset_lineage_reconciled",
                    details={
                        "dataset_id": str(dataset.id),
                        "purpose": body.purpose,
                        "groups": len(set(families.values())),
                        "fresh_claim": False,
                        "frozen_dataset_changed": False,
                    },
                )
            )
            return {
                "status": "reconciled_conservatively_exposed",
                "groups": len(set(families.values())),
                "frozen_dataset_changed": False,
                "fresh_test_claim": False,
            }

    @app.get("/v1/ingest/capture-policies/{policy_id}")
    def capture_policy_for_sdk(policy_id: UUID, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        require_store(app)
        with sessions() as session:
            _, policy = active_policy(session, auth["project_id"], policy_id)
            environment = session.get(Environment, auth["environment_id"])
            if environment.name not in policy.environments:
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
        return {"policy": policy.model_dump(mode="json")}

    @app.post("/v1/ingest/samples", status_code=201)
    def capture_sample(body: Sample, auth=Depends(authorize)):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        store = require_store(app)
        project = auth["project_id"]
        ref = None
        try:
            with sessions.begin() as session:
                _, policy = active_policy(session, project, body.policy_id)
                environment = session.get(Environment, auth["environment_id"])
                if (
                    environment.name != body.environment
                    or not policy.permits(body.environment, body.workflow)
                    or body.provider not in policy.allowed_providers
                ):
                    raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                if body.sample_id.int / (2**128) >= policy.sample_rate:
                    raise HTTPException(403, "SAMPLE_OUTSIDE_APPROVED_RATE")
                existing = session.get(PayloadSample, body.sample_id)
                if existing:
                    if existing.project_id != project:
                        raise HTTPException(403, "SAMPLE_SCOPE_DENIED")
                    return {"status": "duplicate", "sample_id": str(body.sample_id)}
                count = policy_object_count(session, body.policy_id)
                if count >= policy.max_samples:
                    raise HTTPException(429, "CAPTURE_SAMPLE_QUOTA_EXCEEDED")
                case = redact(
                    {
                        "id": str(body.sample_id),
                        "input": body.input,
                        "reference": body.reference,
                        "gold": None,
                        "group": None,
                        "stratum": "default",
                    },
                    policy.never_capture_fields,
                )
                blob = json.dumps(case).encode()
                if len(blob) > policy.max_payload_bytes:
                    raise HTTPException(413, "SAMPLE_EXCEEDS_POLICY_BYTES")
                ref = store.put(project, blob, policy.retention_days)
                session.add(
                    PayloadSample(
                        id=body.sample_id,
                        project_id=project,
                        policy_id=body.policy_id,
                        object_ref=ref,
                        content_hash=digest(case),
                        expires_at=datetime.now(UTC) + timedelta(days=policy.retention_days),
                        metadata_safe={
                            "environment": body.environment,
                            "workflow": body.workflow,
                            "event_id": str(body.event_id),
                            "ordinal": 0,
                            "redaction": policy.redaction_version,
                        },
                    )
                )
            return {"status": "stored", "sample_id": str(body.sample_id)}
        except BaseException:
            if ref:
                store.delete(project, ref)
            raise

    @app.get("/v1/projects/{project_id}/experiments/plans")
    def plans(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "dataset_id": str(row.dataset_id),
                    "specification": row.specification["public_specification"],
                    "plan_hash": row.specification["plan_hash"],
                    "approval_status": row.approval_status,
                }
                for row in session.scalars(
                    select(ExperimentPlanRecord)
                    .where(
                        ExperimentPlanRecord.project_id == project_id,
                        target_evaluation_clause(),
                    )
                    .order_by(ExperimentPlanRecord.id)
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.post("/v1/projects/{project_id}/capture-policies", status_code=201)
    def create_policy(
        project_id: UUID,
        body: CapturePolicy,
        request_id: UUID4 | None = None,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("admin",))
        if body.enabled:
            require_store(app)
            if body.expires_at <= datetime.now(UTC):
                raise HTTPException(422, "POLICY_ALREADY_EXPIRED")
        with sessions.begin() as session:
            if request_id is not None:
                from cutback.server.dataset_lineage import lock_preparation_identity

                lock_preparation_identity(session, "capture-policy", request_id)
                previous = session.get(CapturePolicyRecord, request_id, with_for_update=True)
                if previous is not None:
                    if (
                        previous.project_id != project_id
                        or previous.policy != body.model_dump(mode="json")
                        or previous.enabled != body.enabled
                    ):
                        raise HTTPException(409, "PREPARATION_IDENTITY_CONFLICT")
                    return {"id": str(previous.id), "enabled": previous.enabled}
            row = CapturePolicyRecord(
                **({"id": request_id} if request_id is not None else {}),
                project_id=project_id,
                enabled=body.enabled,
                approved_by=auth["actor"],
                policy=body.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="capture_policy_created",
                    details={"policy_id": str(row.id), "enabled": body.enabled},
                )
            )
        return {"id": str(row.id), "enabled": row.enabled}

    @app.get("/v1/projects/{project_id}/capture-policies")
    def policies(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "enabled": row.enabled,
                    "policy": row.policy,
                    "approved_by": row.approved_by,
                }
                for row in session.scalars(
                    select(CapturePolicyRecord)
                    .where(CapturePolicyRecord.project_id == project_id)
                    .order_by(CapturePolicyRecord.created_at.desc())
                    .limit(100)
                )
            ]

    @app.delete("/v1/projects/{project_id}/capture-policies/{policy_id}")
    def revoke_policy(project_id: UUID, policy_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(CapturePolicyRecord, policy_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "POLICY_NOT_FOUND")
            row.enabled = False
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="capture_policy_revoked",
                    details={"policy_id": str(policy_id)},
                )
            )
        return {"status": "revoked", "future_reads_and_runs": "denied"}

    @app.post("/v1/projects/{project_id}/datasets", status_code=201)
    def import_dataset(
        project_id: UUID,
        body: DatasetImport,
        auth=Depends(authorize),
        *,
        request_id: UUID4 | None = None,
    ):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        stored: list[str] = []
        try:
            with sessions.begin() as session:
                extra = None
                if request_id is not None:
                    from cutback.server.dataset_lineage import lock_preparation_identity

                    lock_preparation_identity(session, "dataset-import", request_id)
                    identity = digest(body.model_dump(mode="json"))
                    extra = {"client_preparation_sha256": identity}
                    previous = session.get(DatasetRecord, request_id, with_for_update=True)
                    if previous is not None:
                        if (
                            previous.project_id != project_id
                            or previous.provenance.get("client_preparation_sha256") != identity
                        ):
                            raise HTTPException(409, "PREPARATION_IDENTITY_CONFLICT")
                        from cutback.server.worker import load_dataset

                        # Current authorization, retained content and immutable
                        # hashes must still hold; a retry cannot resurrect data.
                        _, policy = active_policy(session, project_id, UUID(body.policy_id))
                        if not policy.permits(body.environment, body.workflow):
                            raise HTTPException(403, "CAPTURE_SCOPE_DENIED")
                        try:
                            load_dataset(session, previous, store)
                        except ValueError:
                            raise HTTPException(
                                409, "PREPARATION_CONTENT_NO_LONGER_AVAILABLE"
                            ) from None
                        return {
                            "id": str(previous.id),
                            "immutable_hash": previous.immutable_hash,
                            "provenance": previous.provenance,
                            "content": "encrypted_separate_store",
                        }
                return persist_dataset(
                    session,
                    store,
                    project_id,
                    body,
                    auth["actor"],
                    stored,
                    dataset_id=request_id,
                    provenance_extra=extra,
                )
        except BaseException:
            from cutback.server.campaign_content import discard_unreferenced

            discard_unreferenced(sessions, store, project_id, stored)
            raise

    @app.get("/v1/projects/{project_id}/datasets")
    def datasets(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "hash": row.immutable_hash,
                    "workflow": row.workflow_id,
                    "provenance": row.provenance,
                    "created_at": row.created_at.isoformat(),
                }
                for row in session.scalars(
                    select(DatasetRecord)
                    .where(DatasetRecord.project_id == project_id)
                    .order_by(DatasetRecord.created_at.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ]

    @app.post("/v1/projects/{project_id}/experiments/draft", status_code=201)
    def create_draft(project_id: UUID, body: DraftInput, auth=Depends(authorize)):
        from cutback.evals.planning import draft_plan

        scope(auth, project_id, ("analyst",))
        with sessions() as session:
            row = session.get(DatasetRecord, body.dataset_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            metadata = {
                "hash": row.immutable_hash,
                "workflow": row.workflow_id,
                "provenance": row.provenance,
            }
        try:
            spec = draft_plan(
                metadata,
                body.baseline,
                candidates=body.candidates,
                minimum=body.minimum,
                max_usd=body.max_usd,
                max_requests=body.max_requests,
                repeats=body.repeats,
                pilot_samples=body.pilot_samples,
                pilot_minimum=body.pilot_minimum,
                max_wall_seconds=body.max_wall_seconds,
                criteria=body.criteria,
                retain_case_outputs=body.retain_case_outputs,
                episode_judging=body.episode_judging,
                episode_judge=body.episode_judge,
                episode_max_turns=body.episode_max_turns,
                user_simulation=body.user_simulation,
                isolated_tools=body.isolated_tools,
                security_suite=body.security_suite,
            )
            spec = spec.model_copy(update={"name": body.name})
        except ValueError as exc:
            code = str(exc)
            raise HTTPException(
                422, code if code.isupper() and len(code) < 100 else "INVALID_DRAFT_CONTRACT"
            ) from None
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=spec), auth
        )

    @app.post("/v1/projects/{project_id}/retrieval/draft", status_code=201)
    def retrieval_draft(project_id: UUID, body: RetrievalDraftInput, auth=Depends(authorize)):
        from cutback.evals.retrieval_planning import draft_retrieval_plan

        scope(auth, project_id, ("analyst",))
        require_store(app)
        with sessions() as session:
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            metadata = {
                "hash": dataset.immutable_hash,
                "workflow": dataset.workflow_id,
                "provenance": dataset.provenance,
            }
        try:
            plan = draft_retrieval_plan(
                metadata,
                body.baseline,
                body.corpus,
                body.settings,
                **body.model_dump(
                    exclude={
                        "dataset_id",
                        "baseline",
                        "corpus",
                        "settings",
                        "metric",
                        "budget",
                        "candidates",
                    }
                ),
                metric=body.metric,
                budget=body.budget,
                candidates=body.candidates,
            )
        except ValueError:
            raise HTTPException(422, "RETRIEVAL_DRAFT_CONTRACT_INVALID") from None
        return create_plan(
            project_id, PlanInput(dataset_id=body.dataset_id, specification=plan), auth
        )

    @app.post("/v1/projects/{project_id}/retrieval/confirmation", status_code=201)
    def retrieval_confirmation(
        project_id: UUID, body: RetrievalConfirmationInput, auth=Depends(authorize)
    ):
        from cutback.evals.retrieval_planning import confirmation_plan

        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        with sessions() as session:
            run = session.get(ExperimentRunRecord, body.validation_run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_VALIDATION_NOT_FOUND")
            row = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                prior = load_plan(store, project_id, row.specification)
                plan = confirmation_plan(prior, run.results, str(run.id), body.budget)
            except ValueError:
                raise HTTPException(409, "RETRIEVAL_CONFIRMATION_GATES_NOT_PASSED") from None
            dataset_id = row.dataset_id
        return create_plan(project_id, PlanInput(dataset_id=dataset_id, specification=plan), auth)

    @app.post(
        "/v1/projects/{project_id}/retrieval/{run_id}/proposal", response_model=RetrievalProposal
    )
    def retrieval_proposal(
        project_id: UUID, run_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        from cutback.server.dataset_lineage import lock_lineage
        from cutback.server.stages import validate_confirmation

        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            run = session.get(ExperimentRunRecord, run_id)
            if not run or run.project_id != project_id or run.status != "COMPLETED":
                raise HTTPException(404, "COMPLETED_CONFIRMATION_NOT_FOUND")
            row = session.get(ExperimentPlanRecord, run.plan_id)
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                plan = load_plan(store, project_id, row.specification)
                if not plan.retrieval_execution or not plan.predecessor_run_id:
                    raise ValueError("CONFIRMATION_REQUIRED")
                validate_confirmation(session, store, project_id, plan, run_id=run.id)
            except ValueError:
                raise HTTPException(409, "RETRIEVAL_CONFIRMATION_REQUIRED") from None
            decision: dict = next(iter(run.results.get("recommendations", [])), {})
            if (
                not run.results.get("billing_complete")
                or not decision.get("quality_gate_passed")
                or not decision.get("retrieval_automation_eligible")
                or run.results.get("lineage", {}).get("fresh_test_claim") is not True
            ):
                raise HTTPException(409, "RETRIEVAL_CONFIRMATION_GATES_NOT_PASSED")
            retrieval = plan.retrieval_execution
            if not retrieval.configuration_binding:
                raise HTTPException(409, "RETRIEVAL_SOURCE_CONFIGURATION_BINDING_REQUIRED")
            dataset = session.get(DatasetRecord, row.dataset_id)
            state: Literal[
                "synthetic_local_review_only", "heldout_review_required", "sample_review_required"
            ] = (
                "synthetic_local_review_only"
                if dataset.provenance["source_type"] == "synthetic_fixture"
                else (
                    "heldout_review_required"
                    if decision["status"] == "VERIFIED_ON_HELDOUT_DATASET"
                    else "sample_review_required"
                )
            )
            result = RetrievalProposal(
                run_id=run.id,
                plan_hash=row.specification["plan_hash"],
                dataset_hash=plan.dataset_hash,
                binding=retrieval.configuration_binding,
                baseline=retrieval.variants[0].settings,
                proposed=retrieval.variants[1].settings,
                decision=state,
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="retrieval_configuration_proposal_read",
                    details={"run_id": str(run.id), "purpose": body.purpose},
                )
            )
            return result

    @app.post("/v1/projects/{project_id}/experiments/plans", status_code=201)
    def create_plan(project_id: UUID, body: PlanInput, auth=Depends(authorize)):
        if body.specification.routing_execution is not None:
            scope(auth, project_id, ("admin",))
            if (
                getattr(app.state, "demo_routing_project", None) == str(project_id)
                and body.specification.routing_execution.transport != "scripted_test_only"
            ):
                raise HTTPException(409, "ROUTING_DEMO_REQUIRES_FIXTURE_RESPONSES")
        scope(auth, project_id, ("analyst",))
        from cutback.server.campaign_content import content_transaction

        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            from cutback.server.dataset_lineage import lock_lineage
            from cutback.server.prompt_experiments import register_bindings, validate_bindings

            lock_lineage(session, project_id)
            loaded_prompts = validate_bindings(
                session, require_store(app), project_id, body.specification, auth
            )
            from cutback.server.criteria_service import validate_binding as validate_criteria

            try:
                reviewed_criteria = validate_criteria(
                    session, store, project_id, body.specification
                )
            except ValueError:
                raise HTTPException(409, "EXACT_CURRENT_REVIEWED_CRITERIA_REQUIRED") from None
            from cutback.server.stages import validate_stage

            try:
                validate_stage(session, require_store(app), project_id, body.specification)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            binding = body.specification.source_binding
            from cutback.server.scenario_service import require_current_import

            try:
                require_current_import(session, store, dataset)
                if (
                    dataset.provenance.get("generated_scenario")
                    and body.specification.split != "validation"
                ):
                    raise ValueError("SCENARIO_DEVELOPMENT_VALIDATION_ONLY")
            except ValueError:
                raise HTTPException(409, "CURRENT_GENERATED_DEVELOPMENT_DATASET_REQUIRED") from None
            from cutback.server.routing_service import validate_binding as validate_routing

            try:
                routing_source = validate_routing(
                    session, store, project_id, body.specification, dataset_id=dataset.id
                )
            except ValueError:
                raise HTTPException(409, "EXACT_CURRENT_ROUTING_TRAINING_OR_FIT_REQUIRED") from None
            if binding:
                registered = session.scalars(
                    select(ScanRun)
                    .where(ScanRun.project_id == project_id)
                    .order_by(ScanRun.created_at.desc())
                    .limit(100)
                ).all()
                if not any(
                    binding.model_dump(mode="json") in row.inventory.get("request_profiles", [])
                    for row in registered
                ):
                    raise HTTPException(409, "SOURCE_REQUEST_PROFILE_NOT_REGISTERED")
            _, policy = active_policy(session, project_id, UUID(dataset.provenance["policy_id"]))
            isolated = (
                body.specification.episode_execution.isolated_tools
                if body.specification.episode_execution
                else None
            )
            if isolated and (
                isolated.project_id != project_id
                or isolated.environment != dataset.provenance["environment"]
            ):
                raise HTTPException(403, "ISOLATED_TOOL_PLAN_DATASET_SCOPE_DENIED")
            if body.specification.workflow != dataset.workflow_id or any(
                p not in policy.allowed_providers for p in body.specification.approved_providers
            ):
                raise HTTPException(403, "DATASET_OR_VENDOR_SCOPE_DENIED")
            try:
                manifest = dataset_manifest(session, dataset)
                if (
                    body.specification.routing_execution
                    and body.specification.routing_execution.source
                ):
                    from cutback.core.input_routing import (
                        SOURCE_DATASET_CASE_BOUND,
                        SOURCE_DATASET_CONTENT_BOUND,
                    )
                    from cutback.server.worker import load_dataset

                    # The metadata manifest cannot prove UTF-8 input bounds.
                    # Read authorized encrypted cases rather than infer or omit them.
                    manifest, _ = load_dataset(
                        session,
                        dataset,
                        store,
                        max_cases=SOURCE_DATASET_CASE_BOUND,
                        max_content_bytes=SOURCE_DATASET_CONTENT_BOUND,
                    )
                summary = plan_summary(body.specification, manifest)
                from cutback.evals.input_groups import effective_input_groups, requires_projection

                if requires_projection(body.specification):
                    from cutback.server.worker import load_dataset

                    loaded, _ = load_dataset(session, dataset, store)
                    _, grouping = effective_input_groups(body.specification, loaded)
                    summary["input_grouping"] = grouping
            except ValueError as exc:
                code = str(exc)
                raise HTTPException(
                    422,
                    code
                    if len(code) < 120
                    and code.isascii()
                    and code.isupper()
                    and code.replace("_", "").isalpha()
                    else "INVALID_DATASET_PLAN_CONTRACT",
                ) from None
            if body.request_id:
                from cutback.server.dataset_lineage import lock_preparation_identity

                lock_preparation_identity(session, "experiment-plan", body.request_id)
                previous = session.get(ExperimentPlanRecord, body.request_id, with_for_update=True)
                if previous:
                    if (
                        previous.project_id != project_id
                        or previous.dataset_id != body.dataset_id
                        or previous.specification["plan_hash"]
                        != digest(body.specification.model_dump(mode="json"))
                    ):
                        raise HTTPException(409, "PLAN_REQUEST_ID_CONTENT_OR_SCOPE_CHANGED")
                    try:
                        load_plan(store, project_id, previous.specification)
                    except ValueError:
                        raise HTTPException(409, "PLAN_REQUEST_ID_CONTENT_UNAVAILABLE") from None
                    return {
                        "id": str(previous.id),
                        "status": "EXISTING_PLAN",
                        "approval_status": previous.approval_status,
                        "existing_plan": True,
                        **summary,
                    }
            if (
                session.scalar(
                    select(func.count())
                    .select_from(ExperimentPlanRecord)
                    .where(ExperimentPlanRecord.project_id == project_id)
                )
                >= 1000
            ):
                raise HTTPException(429, "PROJECT_PLAN_QUOTA_EXCEEDED")
            store = require_store(app)
            try:
                frozen = freeze_plan(
                    store, project_id, body.specification, policy, dataset.provenance["policy_id"]
                )
                if loaded_prompts:
                    frozen["expires_at"] = min(
                        datetime.fromisoformat(frozen["expires_at"]),
                        *(graph.expires_at for _, graph in loaded_prompts),
                    ).isoformat()
                if reviewed_criteria is not None:
                    frozen["expires_at"] = min(
                        datetime.fromisoformat(frozen["expires_at"]), reviewed_criteria.expires_at
                    ).isoformat()
                if routing_source is not None:
                    frozen["expires_at"] = min(
                        datetime.fromisoformat(frozen["expires_at"]),
                        routing_source[0].expires_at,
                    ).isoformat()
            except ValueError:
                raise HTTPException(422, "PLAN_CONTENT_POLICY_DENIED") from None
            refs.append(frozen["artifact_ref"])
            row = ExperimentPlanRecord(
                id=body.request_id or uuid4(),
                project_id=project_id,
                dataset_id=dataset.id,
                specification=frozen,
            )
            session.add(row)
            session.flush()
            register_bindings(session, project_id, row.id, body.specification, auth, loaded_prompts)
            from cutback.server.criteria_service import register_binding as register_criteria

            register_criteria(session, project_id, row.id, body.specification, reviewed_criteria)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_planned",
                    details={"plan_id": str(row.id), **summary},
                )
            )
        return {"id": str(row.id), "status": "READY_FOR_APPROVAL", **summary}

    @app.post("/v1/projects/{project_id}/experiments/plans/{plan_id}/content")
    def read_plan_content(
        project_id: UUID, plan_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            row = session.get(ExperimentPlanRecord, plan_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            active_policy(session, project_id, UUID(row.specification["policy_id"]))
            try:
                plan = load_plan(store, project_id, row.specification)
                from cutback.server.prompt_experiments import recheck_bindings

                recheck_bindings(session, store, project_id, row, plan)
                from cutback.server.criteria_service import validate_binding as validate_criteria

                validate_criteria(session, store, project_id, plan)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="plan_content_read",
                    details={"plan_id": str(plan_id), "purpose": body.purpose},
                )
            )
        return {
            "specification": plan.model_dump(mode="json"),
            "plan_hash": row.specification["plan_hash"],
        }

    @app.get("/v1/projects/{project_id}/experiments/plans/{plan_id}/execution")
    def plan_execution(project_id: UUID, plan_id: UUID, auth=Depends(authorize)):
        """Reconcile a lost queue acknowledgement without replaying an approved run."""
        scope(auth, project_id)
        with sessions() as session:
            plan = session.get(ExperimentPlanRecord, plan_id)
            if not plan or plan.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            runs = list(
                session.scalars(
                    select(ExperimentRunRecord)
                    .where(
                        ExperimentRunRecord.project_id == project_id,
                        ExperimentRunRecord.plan_id == plan_id,
                    )
                    .limit(2)
                )
            )
            if len(runs) > 1:
                raise HTTPException(409, "PLAN_EXECUTION_IDENTITY_AMBIGUOUS")
            return {
                "plan_id": str(plan.id),
                "approval_status": plan.approval_status,
                "run_id": str(runs[0].id) if runs else None,
                "run_status": runs[0].status if runs else None,
            }

    @app.post("/v1/projects/{project_id}/experiments/plans/{plan_id}/approve")
    def approve_plan(project_id: UUID, plan_id: UUID, body: ApproveInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(ExperimentPlanRecord, plan_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            if row.specification.get("public_specification", {}).get("operation") in {
                "criteria-generation-v1",
                "criteria-generation-v2",
                "scenario-generation-v1",
            }:
                raise HTTPException(409, "CRITERIA_REQUIRES_ITS_SCOPED_GENERATION_APPROVAL")
            if row.approval_status != "pending":
                raise HTTPException(409, "PLAN_ALREADY_DECIDED")
            if row.specification["plan_hash"] != body.plan_hash:
                raise HTTPException(409, "PLAN_CHANGED")
            row.approval_status, row.approved_by = "approved", auth["actor"]
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_budget_approved",
                    details={
                        "plan_id": str(row.id),
                        "plan_hash": body.plan_hash,
                        "approval_ref": body.approval_ref,
                    },
                )
            )
        return {"status": "APPROVED", "provider_calls_made": 0}

    @app.post("/v1/projects/{project_id}/experiments", status_code=202)
    def queue_run(project_id: UUID, body: QueueInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            from cutback.server.dataset_lineage import lock_lineage, reserve_evaluation

            lock_lineage(session, project_id)
            plan = session.get(ExperimentPlanRecord, body.plan_id, with_for_update=True)
            if not plan or plan.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            if plan.approval_status != "approved":
                raise HTTPException(403, "NO_API_BUDGET_OR_APPROVAL_ALREADY_CONSUMED")
            from cutback.server.stages import validate_stage

            try:
                frozen = load_plan(require_store(app), project_id, plan.specification)
                from cutback.server.scenario_service import require_current_import

                require_current_import(
                    session, require_store(app), session.get(DatasetRecord, plan.dataset_id)
                )
                from cutback.server.prompt_experiments import recheck_bindings

                recheck_bindings(session, require_store(app), project_id, plan, frozen)
                from cutback.server.criteria_service import validate_binding as validate_criteria

                validate_criteria(session, require_store(app), project_id, frozen)
                validate_stage(
                    session,
                    require_store(app),
                    project_id,
                    frozen,
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            plan.approval_status = (
                "consumed"  # A budget approval authorizes one run, not unlimited runs.
            )
            row = ExperimentRunRecord(
                project_id=project_id, plan_id=plan.id, status="QUEUED", results={}
            )
            session.add(row)
            session.flush()
            try:
                row.results = {
                    "lineage": reserve_evaluation(
                        session, project_id, plan.dataset_id, frozen, row.id
                    )
                }
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            session.add(JobRecord(project_id=project_id, run_id=row.id, status="QUEUED"))
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_queued",
                    details={"run_id": str(row.id)},
                )
            )
        return {"id": str(row.id), "status": "QUEUED", "provider_calls_made_in_http": 0}

    @app.get("/v1/projects/{project_id}/experiments")
    def runs(
        project_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            rows = session.execute(
                select(ExperimentRunRecord, ExperimentPlanRecord)
                .join(ExperimentPlanRecord, ExperimentPlanRecord.id == ExperimentRunRecord.plan_id)
                .where(
                    ExperimentRunRecord.project_id == project_id,
                    target_evaluation_clause(),
                )
                .order_by(ExperimentRunRecord.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
            return [
                {
                    "id": str(row.id),
                    "plan_id": str(row.plan_id),
                    "status": row.status,
                    "reserved_usd": str(row.reserved_usd),
                    "spent_usd": str(row.spent_usd),
                    "requests": row.requests_used,
                    "block_reason": row.results.get("code") if row.status == "BLOCKED" else None,
                    "retry_after": row.results.get("retry_after")
                    if row.status == "BLOCKED"
                    else None,
                    "specification": plan.specification["public_specification"],
                    "created_at": row.created_at.isoformat(),
                    "decisions": row.results.get("recommendations", []),
                }
                for row, plan in rows
            ]

    @app.get("/v1/projects/{project_id}/experiments/{run_id}")
    def run_report(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(ExperimentRunRecord, run_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            plan = session.get(ExperimentPlanRecord, row.plan_id)
            return {
                "id": str(row.id),
                "status": row.status,
                "results": row.results,
                "specification": plan.specification["public_specification"],
                "reserved_usd": str(row.reserved_usd),
                "spent_usd": str(row.spent_usd),
                "requests": row.requests_used,
            }

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/cancel")
    def cancel(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            from cutback.server.dataset_lineage import lock_lineage
            from cutback.server.provider_batch_db import ProviderBatchRecord

            lock_lineage(session, project_id)
            row = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            if row.status not in {"QUEUED", "BLOCKED", "RUNNING"}:
                raise HTTPException(409, "RUN_TERMINAL")
            row.status = "CANCELLED"
            from cutback.server.tool_db import ToolSessionRecord

            for tool_session in session.scalars(
                select(ToolSessionRecord)
                .where(
                    ToolSessionRecord.run_id == row.id,
                    ToolSessionRecord.project_id == project_id,
                    ToolSessionRecord.cleanup_confirmed.is_(False),
                )
                .with_for_update()
            ):
                tool_session.cleanup_requested = True
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == row.id).with_for_update()
            )
            if job:
                job.status = "CANCELLED"
            batch = session.scalar(
                select(ProviderBatchRecord)
                .where(
                    ProviderBatchRecord.project_id == project_id,
                    ProviderBatchRecord.run_id == run_id,
                )
                .with_for_update()
            )
            if batch and batch.state not in {"COMPLETED", "CANCELLED", "FAILED", "CLEANUP_PENDING"}:
                if batch.started_at is None:
                    batch.state, batch.completed_at = "CANCELLED", datetime.now(UTC)
                    batch.report = {
                        "code": "BATCH_CANCELLED_BEFORE_EXECUTION",
                        "billing_complete": True,
                        "final_state": "CANCELLED",
                    }
                else:
                    batch.state, batch.available_at = "CANCEL_REQUESTED", datetime.now(UTC)
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_cancelled",
                    details={"run_id": str(row.id)},
                )
            )
        return {"status": "CANCELLED", "inflight_call": "may_finish_no_further_calls"}

    @app.post("/v1/projects/{project_id}/experiments/{run_id}/recheck")
    def recheck_blocked(project_id: UUID, run_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RUN_NOT_FOUND")
            if row.status != "BLOCKED" or row.requests_used:
                raise HTTPException(409, "ONLY_UNSTARTED_BLOCKED_RUNS_CAN_RECHECK")
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == row.id).with_for_update()
            )
            row.status = job.status = "QUEUED"
            job.available_at = None
            row.results = {}
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="experiment_eligibility_rechecked",
                    details={"run_id": str(run_id), "provider_calls": 0},
                )
            )
        return {"status": "QUEUED", "provider_calls": 0}

    @app.get("/v1/projects/{project_id}/recommendations")
    def recommendations(
        project_id: UUID,
        run_id: UUID | None = None,
        offset: int = Query(0, ge=0, le=100000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "run_id": str(row.run_id),
                    "status": row.status,
                    "evidence": row.evidence,
                }
                for row in session.scalars(
                    select(RecommendationRecord)
                    .where(RecommendationRecord.project_id == project_id)
                    .where(*([RecommendationRecord.run_id == run_id] if run_id else []))
                    .order_by(RecommendationRecord.id)
                    .offset(offset)
                    .limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/recommendations/{recommendation_id}/review")
    def review(
        project_id: UUID, recommendation_id: UUID, body: ReviewInput, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            from cutback.server.dataset_lineage import lock_lineage, require_current_test_evidence

            lock_lineage(session, project_id)
            row = session.get(RecommendationRecord, recommendation_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "RECOMMENDATION_NOT_FOUND")
            if row.status != "pending":
                raise HTTPException(409, "RECOMMENDATION_ALREADY_REVIEWED")
            from cutback.core.evaluation_criteria import source_evaluator_eligible

            if body.decision == "accepted_for_staging" and not source_evaluator_eligible(
                row.evidence
            ):
                raise HTTPException(409, "EVALUATOR_REAPPROVAL_REQUIRED")
            if body.decision == "accepted_for_staging" and (
                not row.evidence.get("quality_gate_passed")
                or not row.evidence.get("cheaper_on_sample")
            ):
                raise HTTPException(409, "REQUIRED_COST_AND_QUALITY_GATES_NOT_PASSED")
            if body.decision == "accepted_for_staging":
                run = session.get(ExperimentRunRecord, row.run_id)
                plan = session.get(ExperimentPlanRecord, run.plan_id)
                if plan.specification["public_specification"]["split"] != "test":
                    raise HTTPException(409, "VALIDATION_SEARCH_REQUIRES_TEST_CONFIRMATION")
                if row.evidence.get("lineage", {}).get("fresh_test_claim") is not True:
                    raise HTTPException(409, "TEST_LINEAGE_REAPPROVAL_REQUIRED")
                try:
                    require_current_test_evidence(session, require_store(app), row)
                except ValueError:
                    raise HTTPException(409, "TEST_LINEAGE_REAPPROVAL_REQUIRED") from None
            row.status = body.decision
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="recommendation_reviewed",
                    details={"recommendation_id": str(row.id), **body.model_dump()},
                )
            )
        return {"status": body.decision, "deployment": "not_performed"}

    from cutback.server.prompt_playground import install_prompt_playground

    install_prompt_playground(app, sessions, authorize, scope, create_plan)
    from cutback.server.compression_api import install_compression

    install_compression(app, sessions, authorize, scope, create_plan)
    from cutback.server.joint_context_api import install_joint_context

    install_joint_context(app, sessions, authorize, scope, create_plan)
    from cutback.server.criteria_api import install_criteria

    install_criteria(app, sessions, authorize, scope, create_plan)
    from cutback.server.scenario_api import install_scenario

    install_scenario(app, sessions, authorize, scope)
    from cutback.server.routing_api import install_routing

    install_routing(app, sessions, authorize, scope, create_plan)
    return import_dataset
