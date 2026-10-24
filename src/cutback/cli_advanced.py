"""Optional operational commands. Scan/import paths never activate these actions."""

import hashlib
import os
import time
from pathlib import Path
from uuid import UUID

import typer


def register(app, output, request):
    from cutback.cli_workflows import read_json

    training, schedules = typer.Typer(no_args_is_help=True), typer.Typer(no_args_is_help=True)
    app.add_typer(training, name="training")
    app.add_typer(schedules, name="schedules")

    @training.command("model-hash")
    def model_hash(directory: Path):
        from cutback.evals.training import model_digest

        output(
            {"sha256": model_digest(directory), "model_executed": False, "files_uploaded": False}
        )

    @training.command("list")
    def jobs(project: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(request("GET", endpoint, f"/v1/projects/{project}/training"))

    @training.command("propose")
    def propose(project: UUID, file: Path, endpoint: str = "http://127.0.0.1:8080"):
        from cutback.server.training_api import TrainingInput

        body = TrainingInput.model_validate(read_json(file))
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/training",
                json=body.model_dump(mode="json"),
            )
        )

    @training.command("approve")
    def approve(
        project: UUID,
        training_id: UUID,
        approval_hash: str,
        approval_ref: str,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        from cutback.server.continuous import Approval

        body = Approval(approval_hash=approval_hash, approval_ref=approval_ref)
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/training/{training_id}/approve",
                json=body.model_dump(),
            )
        )

    @training.command("cancel")
    def cancel(project: UUID, training_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(
            request(
                "POST", endpoint, f"/v1/projects/{project}/training/{training_id}/cancel", json={}
            )
        )

    @training.command("worker-once")
    def worker_once():
        from cutback.evals.artifacts import configured_store
        from cutback.server.db import database
        from cutback.server.training_worker import run_training_one

        if os.getenv("CUTBACK_TRAINING_EXECUTION", "false").lower() != "true":
            output({"status": "TRAINING_EXECUTION_DISABLED"})
            return
        engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
        try:
            output(run_training_one(sessions, configured_store(), execution_enabled=True))
        finally:
            engine.dispose()

    @training.command("download")
    def download(
        project: UUID,
        training_id: UUID,
        artifact_index: int,
        destination: Path,
        purpose: str,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        import httpx

        from cutback.core.config import validate_endpoint
        from cutback.server.training_api import ArtifactAccess

        token = os.environ["CUTBACK_CONTROL_TOKEN"]
        body = ArtifactAccess(purpose=purpose)
        checksum, total, started = hashlib.sha256(), 0, time.monotonic()
        created = False
        try:
            with httpx.stream(
                "POST",
                validate_endpoint(endpoint)
                + f"/v1/projects/{project}/training/{training_id}/artifacts/{artifact_index}",
                headers={"Authorization": "Bearer " + token},
                json=body.model_dump(),
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as response:
                response.raise_for_status()
                expected = response.headers["X-Content-SHA256"]
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
                with os.fdopen(descriptor, "wb") as stream:
                    for chunk in response.iter_bytes(500000):
                        total += len(chunk)
                        if total > 128000000 or time.monotonic() - started > 60:
                            raise ValueError("ARTIFACT_DOWNLOAD_BOUND")
                        checksum.update(chunk)
                        stream.write(chunk)
                if checksum.hexdigest() != expected:
                    raise ValueError("ARTIFACT_CHECKSUM_MISMATCH")
            output({"file": str(destination), "sha256": expected, "bytes": total})
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise

    @schedules.command("list")
    def schedule_list(project: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(request("GET", endpoint, f"/v1/projects/{project}/schedules"))

    @schedules.command("propose")
    def schedule_propose(project: UUID, file: Path, endpoint: str = "http://127.0.0.1:8080"):
        from cutback.server.continuous import ScheduleInput

        body = ScheduleInput.model_validate(read_json(file))
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/schedules",
                json=body.model_dump(mode="json"),
            )
        )

    @schedules.command("approve")
    def schedule_approve(
        project: UUID,
        schedule_id: UUID,
        approval_hash: str,
        approval_ref: str,
        endpoint: str = "http://127.0.0.1:8080",
    ):
        from cutback.server.continuous import Approval

        body = Approval(approval_hash=approval_hash, approval_ref=approval_ref)
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/schedules/{schedule_id}/approve",
                json=body.model_dump(),
            )
        )

    @schedules.command("disable")
    def schedule_disable(project: UUID, schedule_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(request("DELETE", endpoint, f"/v1/projects/{project}/schedules/{schedule_id}"))

    return training, schedules
