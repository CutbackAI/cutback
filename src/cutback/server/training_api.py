"""Rights-gated, hash-approved local training. Source data and weights stay encrypted."""

import hashlib
import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import CapturePolicy, digest
from cutback.evals.training import TrainingSpec, registered_model
from cutback.server.continuous import Approval
from cutback.server.db import AuditEvent, CapturePolicyRecord, DatasetRecord
from cutback.server.enterprise_db import TrainingJob
from cutback.server.evaluation_api import require_store
from cutback.server.worker import load_dataset


class TrainingInput(StrictModel):
    dataset_id: UUID
    specification: TrainingSpec


class ArtifactAccess(StrictModel):
    purpose: Identifier


def authorize_dataset(session, row, store, spec):
    dataset, policy = load_dataset(session, row, store)
    if row.immutable_hash != spec.dataset_hash or "local" not in policy.allowed_providers:
        raise ValueError("TRAINING_DATASET_OR_LOCAL_PROCESSING_SCOPE_DENIED")
    if spec.retention_days > policy.retention_days:
        raise ValueError("TRAINING_ARTIFACT_RETENTION_EXCEEDS_POLICY")
    counts = {
        split: sum(item["split"] == split for item in dataset["items"])
        for split in ("train", "validation", "test")
    }
    if counts["train"] < 20 or counts["validation"] < 5 or counts["test"] < 5:
        raise ValueError("TRAINING_REQUIRES_SEPARATE_TRAIN_VALIDATION_TEST_GROUPS")
    target = "gold" if spec.target_source == "gold" else "reference"
    if any(
        item["case"].get(target) is None for item in dataset["items"] if item["split"] == "train"
    ):
        raise ValueError("TRAINING_TARGETS_MISSING")
    return dataset, policy, counts


def training_record(row):
    return {
        "id": str(row.id),
        "dataset_id": str(row.dataset_id),
        "status": row.status,
        "specification": row.specification,
        "approval_hash": row.approval_hash,
        "reserved_usd": str(row.reserved_usd),
        "results": row.results,
        "training_enabled": os.getenv("CUTBACK_TRAINING_EXECUTION", "false").lower() == "true",
    }


def install_training(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/training/config")
    def configuration(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        import json

        registry = json.loads(os.getenv("CUTBACK_TRAINING_MODELS", "{}"))
        return {
            "models": [
                {"ref": key, "sha256": value.get("sha256")}
                for key, value in registry.items()
                if isinstance(value, dict)
            ][:100],
            "cpu_hourly_upper_usd": os.getenv("CUTBACK_TRAINING_CPU_HOURLY_USD"),
            "cuda_hourly_upper_usd": os.getenv("CUTBACK_TRAINING_CUDA_HOURLY_USD"),
            "cuda_enabled": os.getenv("CUTBACK_TRAINING_ALLOW_CUDA", "false").lower() == "true",
            "execution_enabled": os.getenv("CUTBACK_TRAINING_EXECUTION", "false").lower() == "true",
        }

    @app.get("/v1/projects/{project_id}/training")
    def jobs(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return [
                training_record(row)
                for row in session.scalars(
                    select(TrainingJob)
                    .where(TrainingJob.project_id == project_id)
                    .order_by(TrainingJob.created_at.desc())
                    .limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/training", status_code=201)
    def propose(project_id: UUID, body: TrainingInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        store = require_store(app)
        spec = body.specification
        try:
            registered_model(spec)
            configured_rate = os.environ["CUTBACK_TRAINING_" + spec.device.upper() + "_HOURLY_USD"]
            if Decimal(configured_rate) != spec.hourly_compute_upper_usd:
                raise ValueError("TRAINING_RATE_DOES_NOT_MATCH_OPERATOR_CONTRACT")
        except (ValueError, KeyError, OSError):
            raise HTTPException(422, "TRAINING_MODEL_OR_COMPUTE_CONTRACT_INVALID") from None
        with sessions.begin() as session:
            if (
                session.scalar(
                    select(func.count())
                    .select_from(TrainingJob)
                    .where(TrainingJob.project_id == project_id)
                )
                >= 1000
            ):
                raise HTTPException(429, "TRAINING_PROJECT_JOB_QUOTA")
            dataset = session.get(DatasetRecord, body.dataset_id)
            if not dataset or dataset.project_id != project_id:
                raise HTTPException(404, "DATASET_NOT_FOUND")
            try:
                _, _, counts = authorize_dataset(session, dataset, store, spec)
            except (ValueError, OSError):
                raise HTTPException(
                    422, "TRAINING_DATASET_RIGHTS_SPLITS_OR_RETENTION_INVALID"
                ) from None
            frozen = spec.model_dump(mode="json")
            row = TrainingJob(
                project_id=project_id,
                dataset_id=body.dataset_id,
                specification=frozen,
                approval_hash=digest(frozen),
                results={
                    "split_counts": counts,
                    "training_targets": spec.target_source,
                    "semantic_quality": "NOT_VERIFIED",
                },
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="training_proposed",
                    details={
                        "training_id": str(row.id),
                        "approval_hash": row.approval_hash,
                        "compute_reservation_usd": str(spec.reservation()),
                        "rights_ref": spec.training_rights_ref,
                    },
                )
            )
        return training_record(row)

    @app.post("/v1/projects/{project_id}/training/{training_id}/approve")
    def approve(project_id: UUID, training_id: UUID, body: Approval, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(TrainingJob, training_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "TRAINING_NOT_FOUND")
            if (
                row.status != "DRAFT"
                or digest(row.specification) != row.approval_hash
                or row.approval_hash != body.approval_hash
            ):
                raise HTTPException(409, "TRAINING_STATE_OR_HASH_MISMATCH")
            spec = TrainingSpec.model_validate(row.specification)
            row.reserved_usd = spec.reservation()
            row.status = "QUEUED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="training_budget_reserved",
                    details={
                        "training_id": str(row.id),
                        "approval_hash": body.approval_hash,
                        "approval_ref": body.approval_ref,
                        "reserved_usd": str(row.reserved_usd),
                    },
                )
            )
        return {
            "status": "QUEUED",
            "reserved_usd": str(row.reserved_usd),
            "execution": "separately_enabled_customer_worker",
        }

    @app.post("/v1/projects/{project_id}/training/{training_id}/cancel")
    def cancel(project_id: UUID, training_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            row = session.get(TrainingJob, training_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "TRAINING_NOT_FOUND")
            if row.status in {"DRAFT", "QUEUED"}:
                row.status = "CANCELLED"
            elif row.status == "RUNNING":
                row.status = "CANCEL_REQUESTED"
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="training_cancel_requested",
                    details={"training_id": str(row.id)},
                )
            )
        return {"status": row.status, "reservation": "not_refunded"}

    @app.post("/v1/projects/{project_id}/training/{training_id}/artifacts/{artifact_index}")
    def artifact(
        project_id: UUID,
        training_id: UUID,
        artifact_index: int,
        body: ArtifactAccess,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("editor",))
        store = require_store(app)
        with sessions.begin() as session:
            row = session.get(TrainingJob, training_id)
            if not row or row.project_id != project_id or row.status != "COMPLETED":
                raise HTTPException(404, "TRAINED_ARTIFACT_NOT_FOUND")
            artifacts = row.results.get("artifacts", [])
            if (
                not 0 <= artifact_index < len(artifacts)
                or row.results.get("expires_at", "") <= datetime.now(UTC).isoformat()
            ):
                raise HTTPException(410, "TRAINED_ARTIFACT_EXPIRED_OR_ABSENT")
            result = artifacts[artifact_index]
            dataset = session.get(DatasetRecord, row.dataset_id)
            authorize_dataset(
                session, dataset, store, TrainingSpec.model_validate(row.specification)
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="trained_artifact_accessed",
                    details={
                        "training_id": str(training_id),
                        "artifact_index": artifact_index,
                        "purpose": body.purpose,
                    },
                )
            )

        def chunks():
            for chunk in result["chunks"]:
                with sessions() as session:
                    policy_row = session.get(CapturePolicyRecord, dataset.provenance["policy_id"])
                    if (
                        not policy_row
                        or not policy_row.enabled
                        or not CapturePolicy.model_validate(policy_row.policy).permits(
                            dataset.provenance["environment"], dataset.workflow_id
                        )
                    ):
                        raise ValueError("TRAINED_ARTIFACT_POLICY_REVOKED")
                data = store.get(project_id, chunk["ref"])
                if hashlib.sha256(data).hexdigest() != chunk["sha256"]:
                    raise ValueError("TRAINED_ARTIFACT_INTEGRITY_FAILURE")
                yield data

        return StreamingResponse(
            chunks(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": 'attachment; filename="' + result["name"] + '"',
                "X-Content-SHA256": result["sha256"],
            },
        )
