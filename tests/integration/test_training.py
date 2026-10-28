import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset

from cutback.evals.training import TrainingSpec, model_digest
from cutback.server.enterprise_db import TrainingJob
from cutback.server.training_worker import expire_training_artifacts, run_training_one

pytestmark = pytest.mark.postgres


def training_spec(dataset, model_hash):
    return {
        "dataset_hash": dataset["immutable_hash"],
        "base_model_ref": "owned-fixture",
        "base_model_sha256": model_hash,
        "training_rights_ref": "synthetic-owned-training",
        "trained_model_owner": "synthetic-tests",
        "training_rights_confirmed": True,
        "max_steps": 2,
        "max_sequence_tokens": 32,
        "max_wall_seconds": 60,
        "max_usd": "0",
        "hourly_compute_upper_usd": "0",
        "retention_days": 1,
    }


def test_training_rights_budget_approval_and_crash_never_replayed(
    api, tenant, monkeypatch, tmp_path
):
    client, admin, app = api
    path, store, dataset, _ = authorized_dataset(api, tenant, tmp_path / "encrypted", monkeypatch)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    checksum = model_digest(model)
    monkeypatch.setenv(
        "CUTBACK_TRAINING_MODELS",
        json.dumps({"owned-fixture": {"path": str(model), "sha256": checksum}}),
    )
    monkeypatch.setenv("CUTBACK_TRAINING_CPU_HOURLY_USD", "0")
    spec = training_spec(dataset, checksum)
    assert run_training_one(app.state.sessions, store)["status"] == "TRAINING_EXECUTION_DISABLED"
    response = client.post(
        path + "/training", headers=admin, json={"dataset_id": dataset["id"], "specification": spec}
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert (
        client.post(
            path + f"/training/{job['id']}/approve",
            headers=admin,
            json={"approval_hash": "a" * 64, "approval_ref": "synthetic"},
        ).status_code
        == 409
    )
    approval = {"approval_hash": job["approval_hash"], "approval_ref": "synthetic-training"}
    assert (
        client.post(
            path + f"/training/{job['id']}/approve", headers=admin, json=approval
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + f"/training/{job['id']}/approve", headers=admin, json=approval
        ).status_code
        == 409
    )
    # No configured trainer: fail safely, keep the reservation and require a new approval/job.
    monkeypatch.delenv("CUTBACK_TRAINING_PYTHON", raising=False)
    assert run_training_one(app.state.sessions, store, execution_enabled=True)["status"] == "FAILED"
    assert run_training_one(app.state.sessions, store, execution_enabled=True)["status"] == "IDLE"
    with app.state.sessions() as session:
        row = session.get(TrainingJob, UUID(job["id"]))
        assert row.status == "FAILED" and row.results["compute_invoice_usd"] is None
    with pytest.raises(ValueError, match="TEACHER_OUTPUT"):
        TrainingSpec.model_validate({**spec, "target_source": "reference"})
    with pytest.raises(ValueError, match="GPU_HOUR"):
        TrainingSpec.model_validate({**spec, "device": "cuda"})


def test_training_cancellation_kills_claimed_process_and_crash_lease_is_not_replayed(
    api, tenant, monkeypatch, tmp_path
):
    import sys
    from datetime import UTC, datetime, timedelta

    client, admin, app = api
    path, store, dataset, _ = authorized_dataset(api, tenant, tmp_path / "encrypted", monkeypatch)
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}", encoding="utf-8")
    checksum = model_digest(model)
    monkeypatch.setenv(
        "CUTBACK_TRAINING_MODELS",
        json.dumps({"owned-fixture": {"path": str(model), "sha256": checksum}}),
    )
    monkeypatch.setenv("CUTBACK_TRAINING_CPU_HOURLY_USD", "0")
    monkeypatch.setenv("CUTBACK_TRAINING_PYTHON", sys.executable)
    job = client.post(
        path + "/training",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": training_spec(dataset, checksum)},
    ).json()
    client.post(
        path + f"/training/{job['id']}/approve",
        headers=admin,
        json={"approval_hash": job["approval_hash"], "approval_ref": "synthetic-cancellation"},
    ).raise_for_status()
    killed = []

    class PendingProcess:
        def __init__(self, *args, **kwargs):
            assert not any("KEY" in name or "TOKEN" in name for name in kwargs["env"])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            with app.state.sessions.begin() as session:
                session.get(TrainingJob, UUID(job["id"])).status = "CANCEL_REQUESTED"
            return None

        def kill(self):
            killed.append(True)

        def wait(self, **kwargs):
            return 0

    with monkeypatch.context() as context:
        context.setattr("cutback.server.training_worker.subprocess.Popen", PendingProcess)
        assert (
            run_training_one(app.state.sessions, store, execution_enabled=True)["status"]
            == "CANCELLED"
        )
    assert killed == [True]
    with app.state.sessions.begin() as session:
        row = session.get(TrainingJob, UUID(job["id"]))
        row.status = "RUNNING"
        row.results = {"lease_expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
    assert run_training_one(app.state.sessions, store, execution_enabled=True)["status"] == "IDLE"
    with app.state.sessions() as session:
        row = session.get(TrainingJob, UUID(job["id"]))
        assert row.status == "FAILED" and row.results["retry"] == "requires_new_job_and_approval"


def test_native_cpu_lora_training_is_train_only_encrypted_and_not_semantic_validation(
    api, tenant, monkeypatch, tmp_path
):
    python = os.getenv("CUTBACK_TEST_TRAINING_PYTHON")
    if not python:
        pytest.skip("set CUTBACK_TEST_TRAINING_PYTHON to the pinned isolated CPU training runtime")
    client, admin, app = api
    path, store, dataset, _ = authorized_dataset(api, tenant, tmp_path / "encrypted", monkeypatch)
    root = Path(__file__).resolve().parents[2]
    model = tmp_path / "owned-model"
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "CUDA_VISIBLE_DEVICES": "",
    }
    result = subprocess.run(
        [python, str(root / "scripts/create_training_fixture.py"), str(model)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    checksum = model_digest(model)
    monkeypatch.setenv(
        "CUTBACK_TRAINING_MODELS",
        json.dumps({"owned-fixture": {"path": str(model), "sha256": checksum}}),
    )
    monkeypatch.setenv("CUTBACK_TRAINING_CPU_HOURLY_USD", "0")
    monkeypatch.setenv("CUTBACK_TRAINING_PYTHON", python)
    response = client.post(
        path + "/training",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": training_spec(dataset, checksum)},
    )
    assert response.status_code == 201, response.text
    job = response.json()
    client.post(
        path + f"/training/{job['id']}/approve",
        headers=admin,
        json={"approval_hash": job["approval_hash"], "approval_ref": "owned-cpu-fixture-only"},
    ).raise_for_status()
    slug = client.get(path, headers=admin).json()["slug"]
    assert client.delete(path, params={"confirm_slug": slug}, headers=admin).status_code == 409
    # Synthetic-only native-runtime diagnostics. Production subprocesses suppress logs.
    original_popen = subprocess.Popen
    diagnostic = tmp_path / "synthetic-training-diagnostics.txt"
    with diagnostic.open("wb") as log:

        def native_process(*args, **kwargs):
            kwargs["stderr"] = log
            return original_popen(*args, **kwargs)

        with monkeypatch.context() as context:
            context.setattr("cutback.server.training_worker.subprocess.Popen", native_process)
            outcome = run_training_one(app.state.sessions, store, execution_enabled=True)
    assert outcome["status"] == "COMPLETED", diagnostic.read_text(encoding="utf-8")
    with app.state.sessions() as session:
        row = session.scalar(select(TrainingJob).where(TrainingJob.id == UUID(job["id"])))
        results = row.results
        assert results["metrics"]["steps"] == 2
        assert results["metrics"]["training_examples"] == results["split_counts"]["train"]
        assert results["training_split"] == "train_only" and results["gpu_hours"] == "0"
        assert (
            results["semantic_quality"] == "NOT_VERIFIED" and results["heldout_evaluation_required"]
        )
        artifacts = results["artifacts"]
        assert len(artifacts) == 2
    for index, artifact in enumerate(artifacts):
        response = client.post(
            path + f"/training/{job['id']}/artifacts/{index}",
            headers=admin,
            json={"purpose": "synthetic-owned-download"},
        )
        assert response.status_code == 200
        import hashlib

        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        assert response.headers["x-content-sha256"] == artifact["sha256"]
    from datetime import UTC, datetime, timedelta

    assert (
        expire_training_artifacts(
            app.state.sessions, store, now=datetime.now(UTC) + timedelta(days=2)
        )["training_artifacts_expired"]
        == 1
    )
    for artifact in artifacts:
        for chunk in artifact["chunks"]:
            assert not store._path(tenant[0], chunk["ref"]).exists()
