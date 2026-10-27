"""One explicit local training process per claimed job; crashed jobs are never replayed."""

import hashlib
import json
import os
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from cutback.evals.datasets import CapturePolicy, digest
from cutback.evals.training import TrainingSpec, registered_model
from cutback.server.content_policy import require_payloads_enabled
from cutback.server.db import AuditEvent, CapturePolicyRecord, DatasetRecord
from cutback.server.enterprise_db import TrainingJob
from cutback.server.training_api import authorize_dataset


def run_training_one(sessions, store, *, execution_enabled=False):
    if not execution_enabled:
        return {"status": "TRAINING_EXECUTION_DISABLED"}
    now = datetime.now(UTC)
    with sessions.begin() as session:
        for lost in session.scalars(
            select(TrainingJob)
            .where(TrainingJob.status.in_(["RUNNING", "CANCEL_REQUESTED"]))
            .with_for_update(skip_locked=True)
        ):
            if lost.results.get("lease_expires_at", "") < now.isoformat():
                lost.status = "FAILED"
                lost.results = {
                    **lost.results,
                    "code": "TRAINING_LEASE_EXPIRED_COMPUTE_BILLING_UNCERTAIN",
                    "retry": "requires_new_job_and_approval",
                }
        job = session.scalar(
            select(TrainingJob)
            .where(TrainingJob.status == "QUEUED")
            .order_by(TrainingJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return {"status": "IDLE"}
        job.status = "RUNNING"
        spec = TrainingSpec.model_validate(job.specification)
        job.results = {
            **job.results,
            "lease_expires_at": (now + timedelta(seconds=spec.max_wall_seconds + 30)).isoformat(),
        }
        job_id, project_id, dataset_id = job.id, job.project_id, job.dataset_id
        approval_hash = job.approval_hash
    created_refs, retained = [], False
    result: dict[str, Any] = {
        "code": "TRAINING_EXECUTION_FAILED_DETAILS_SUPPRESSED",
        "semantic_quality": "NOT_VERIFIED",
    }
    status = "FAILED"
    started = time.monotonic()
    phase = "validate_contract"
    try:
        if digest(spec.model_dump(mode="json")) != approval_hash:
            raise ValueError("TRAINING_APPROVAL_CONTRACT_CHANGED")
        if (
            spec.device == "cuda"
            and os.getenv("CUTBACK_TRAINING_ALLOW_CUDA", "false").lower() != "true"
        ):
            raise ValueError("GPU_EXECUTION_NOT_APPROVED")
        rate = Decimal(os.environ["CUTBACK_TRAINING_" + spec.device.upper() + "_HOURLY_USD"])
        if rate != spec.hourly_compute_upper_usd:
            raise ValueError("TRAINING_COMPUTE_RATE_CHANGED")
        python = Path(os.environ["CUTBACK_TRAINING_PYTHON"])
        if not python.is_absolute() or not python.is_file():
            raise ValueError("EXPLICIT_TRAINING_PYTHON_REQUIRED")
        phase = "verify_base_model"
        base = registered_model(spec, verify=True)
        phase = "authorize_training_data"
        with sessions() as session:
            dataset_row = session.get(DatasetRecord, dataset_id)
            dataset, _, counts = authorize_dataset(session, dataset_row, store, spec)
            provenance = dataset_row.provenance
            workflow = dataset_row.workflow_id
        expiry = min(
            datetime.fromisoformat(dataset["expires_at"]), now + timedelta(days=spec.retention_days)
        )
        records = []
        for item in dataset["items"]:
            if item["split"] == "train":
                case = item["case"]
                target = case[spec.target_source]
                records.append(
                    {
                        "input": case["input"],
                        "target": target
                        if isinstance(target, str)
                        else json.dumps(target, sort_keys=True),
                    }
                )
        del dataset
        with tempfile.TemporaryDirectory(prefix="cutback-training-") as temporary:
            root = Path(temporary).resolve()
            records_path, config_path, output = (
                root / "train.json",
                root / "config.json",
                root / "adapter",
            )
            output.mkdir(mode=0o700)
            for path, value in (
                (records_path, records),
                (
                    config_path,
                    {
                        "specification": spec.model_dump(mode="json"),
                        "base_model": str(base),
                        "records": str(records_path),
                        "output": str(output),
                    },
                ),
            ):
                with os.fdopen(
                    os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                    "w",
                    encoding="utf-8",
                ) as stream:
                    json.dump(value, stream)
            del records
            environment = {
                key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ
            }
            environment.update(
                {
                    "PATH": str(python.parent),
                    "HF_HOME": str(root / "hf"),
                    "TORCHINDUCTOR_CACHE_DIR": str(root / "inductor"),
                    "TORCH_HOME": str(root / "torch"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                    "USERNAME": "cutback-training",
                    "TEMP": str(root),
                    "TMP": str(root),
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "HF_DATASETS_OFFLINE": "1",
                    "OMP_NUM_THREADS": "1",
                    "MKL_NUM_THREADS": "1",
                    "CUDA_VISIBLE_DEVICES": os.getenv("CUTBACK_TRAINING_CUDA_DEVICE", "0")
                    if spec.device == "cuda"
                    else "",
                }
            )
            runner = Path(__file__).resolve().parents[1] / "evals/training_runner.py"
            phase = "native_training_subprocess"
            child_started = time.monotonic()
            with subprocess.Popen(
                [str(python), "-I", str(runner), "--config", str(config_path)],
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as process:
                try:
                    while process.poll() is None:
                        require_payloads_enabled()
                        if (
                            time.monotonic() - child_started >= spec.max_wall_seconds
                            or datetime.now(UTC) >= expiry
                        ):
                            raise TimeoutError("TRAINING_WALL_OR_RETENTION_CAP")
                        with sessions() as session:
                            current = session.get(TrainingJob, job_id)
                            policy_row = session.get(CapturePolicyRecord, provenance["policy_id"])
                            if (
                                not policy_row
                                or not policy_row.enabled
                                or not CapturePolicy.model_validate(policy_row.policy).permits(
                                    provenance["environment"], workflow
                                )
                            ):
                                raise ValueError("TRAINING_POLICY_REVOKED")
                            if current.status != "RUNNING":
                                status = (
                                    "CANCELLED"
                                    if current.status == "CANCEL_REQUESTED"
                                    else "FAILED"
                                )
                                raise ValueError("TRAINING_CANCELLED_OR_LEASE_LOST")
                        time.sleep(0.25)
                    if process.returncode != 0:
                        raise ValueError("TRAINING_SUBPROCESS_FAILED")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
            phase = "retain_encrypted_adapter"
            child_seconds = Decimal(str(time.monotonic() - child_started))
            # Recheck rights before retaining a possibly memorizing adapter.
            with sessions() as session:
                authorize_dataset(session, session.get(DatasetRecord, dataset_id), store, spec)
            metrics_path = output / "metrics.json"
            if metrics_path.stat().st_size > 4096:
                raise ValueError("TRAINING_METRICS_RESOURCE_BOUND")
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            metrics = {
                key: metrics[key]
                for key in (
                    "steps",
                    "training_examples",
                    "first_training_loss",
                    "last_training_loss",
                )
            }
            artifacts = []
            total = 0
            for name in ("adapter_config.json", "adapter_model.safetensors"):
                path = output / name
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("UNSAFE_TRAINING_ARTIFACT")
                total += path.stat().st_size
                if total > 128000000:
                    raise ValueError("TRAINING_ARTIFACT_SIZE_BOUND")
                chunks, checksum = [], hashlib.sha256()
                with path.open("rb") as stream:
                    while chunk := stream.read(500000):
                        require_payloads_enabled()
                        checksum.update(chunk)
                        ref = store.put(project_id, chunk, spec.retention_days)
                        created_refs.append(ref)
                        chunks.append({"ref": ref, "sha256": hashlib.sha256(chunk).hexdigest()})
                artifacts.append(
                    {
                        "name": name,
                        "bytes": path.stat().st_size,
                        "sha256": checksum.hexdigest(),
                        "chunks": chunks,
                    }
                )
            seconds = child_seconds
            result = {
                "artifacts": artifacts,
                "expires_at": expiry.isoformat(),
                "metrics": metrics,
                "split_counts": counts,
                "training_split": "train_only",
                "heldout_evaluation_required": True,
                "semantic_quality": "NOT_VERIFIED",
                "compute_wall_seconds": str(seconds),
                "gpu_hours": str(seconds / 3600) if spec.device == "cuda" else "0",
                "modeled_compute_upper_subtotal_usd": str(rate * seconds / 3600),
                "compute_invoice_usd": None,
                "setup_hosting_cost_usd": None,
                "ownership_ref": spec.trained_model_owner,
            }
            status = "COMPLETED"
        with sessions.begin() as session:
            current = session.get(TrainingJob, job_id, with_for_update=True)
            if current.status == "RUNNING":
                current.status, current.results = status, result
                retained = True
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor="training_worker",
                        action="training_finished",
                        details={
                            "training_id": str(job_id),
                            "status": status,
                            "artifact_count": len(result["artifacts"]),
                        },
                    )
                )
            else:
                status = "CANCELLED" if current.status == "CANCEL_REQUESTED" else "FAILED"
                current.status = status
    except Exception as error:
        result["failure_phase"] = phase
        with sessions.begin() as session:
            current = session.get(TrainingJob, job_id, with_for_update=True)
            if current.status in {"RUNNING", "CANCEL_REQUESTED"}:
                current.status = "CANCELLED" if current.status == "CANCEL_REQUESTED" else status
                current.results = {
                    **current.results,
                    "code": "TRAINING_FAILED_OR_CANCELLED_DETAILS_SUPPRESSED",
                    "failure_phase": phase,
                    "failure_type": type(error).__name__,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "compute_invoice_usd": None,
                    "retry": "requires_new_job_and_approval",
                }
            status = current.status
    finally:
        if not retained:
            for ref in created_refs:
                store.delete(project_id, ref)
    return {
        "status": status,
        "training_id": str(job_id),
        **({"failure_phase": result.get("failure_phase")} if status != "COMPLETED" else {}),
    }


def expire_training_artifacts(sessions, store, *, now=None):
    now = now or datetime.now(UTC)
    count = 0
    with sessions.begin() as session:
        for job in session.scalars(
            select(TrainingJob)
            .where(
                TrainingJob.status == "COMPLETED",
                TrainingJob.results["expires_at"].as_string() <= now.isoformat(),
                TrainingJob.results["artifacts_expired"].as_boolean().is_not(True),
            )
            .order_by(TrainingJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(100)
        ):
            if job.results.get("expires_at", "") > now.isoformat():
                continue
            for artifact in job.results.get("artifacts", []):
                for chunk in artifact["chunks"]:
                    store.delete(job.project_id, chunk["ref"])
            job.results = {**job.results, "artifacts": [], "artifacts_expired": True}
            count += 1
    return {"training_artifacts_expired": count}
