"""Explicit asynchronous provider batch control; never run native requests in the CLI."""

import re
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer


def register_batch(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Approve, inspect and reconcile provider batches."
    )
    app.add_typer(group, name="batch")

    def identity(value):
        try:
            return str(UUID(value))
        except ValueError:
            fail("BATCH_UUID_REQUIRED", "Use the exact project, plan or batch UUID.")

    @group.command("prepare")
    def prepare(
        plan_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        provider_storage_approved: bool = False,
        max_turnaround_seconds: int = 90000,
    ):
        """Prepare the exact budget/storage contract. This does not approve spending."""
        if not provider_storage_approved:
            fail(
                "BATCH_PROVIDER_STORAGE_APPROVAL_REQUIRED",
                "Review provider retention and explicitly pass --provider-storage-approved.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{identity(project)}/provider-batches",
                json={
                    "plan_id": identity(plan_id),
                    "provider_storage_approved": True,
                    "max_turnaround_seconds": max_turnaround_seconds,
                },
            ),
            destination,
        )

    @group.command("show")
    def show(
        batch_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Read metadata, known charges and cleanup state without replaying requests."""
        output(
            request(
                "GET",
                endpoint,
                f"/v1/projects/{identity(project)}/provider-batches/{identity(batch_id)}",
            ),
            destination,
        )

    @group.command("approve")
    def approve(
        batch_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        approval_hash: Annotated[str, typer.Option("--approval-hash")],
        approval_ref: Annotated[str, typer.Option("--approval-ref")],
    ):
        """Consume one reviewed frozen budget and explicit provider storage approval."""
        if not re.fullmatch(r"[a-f0-9]{64}", approval_hash):
            fail(
                "BATCH_APPROVAL_HASH_REQUIRED",
                "Copy the exact hash from the reviewed preparation receipt.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{identity(project)}/provider-batches/{identity(batch_id)}/approve",
                json={"approval_hash": approval_hash, "approval_ref": approval_ref},
            ),
            destination,
        )

    @group.command("cancel")
    def cancel(
        batch_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Request containment; completed native work can still be charged."""
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{identity(project)}/provider-batches/{identity(batch_id)}/cancel",
                json={},
            ),
            destination,
        )

    @group.command("reconcile")
    def reconcile(
        batch_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        hints: Annotated[Path | None, typer.Option("--hints")] = None,
    ):
        """Request read-only recovery; optional JSON hints must match frozen native identities."""
        import json

        body = {}
        if hints is not None:
            try:
                if hints.stat().st_size > 8192:
                    raise ValueError("BATCH_RECOVERY_HINT_BYTE_BOUND")
                body = json.loads(hints.read_text(encoding="utf-8"))
                if not isinstance(body, dict) or set(body) != {"hints"}:
                    raise ValueError("BATCH_RECOVERY_HINT_OBJECT_REQUIRED")
            except (OSError, ValueError, UnicodeError):
                fail(
                    "BATCH_RECOVERY_HINT_FILE_INVALID",
                    "Use a JSON hints object of at most 8192 bytes.",
                )
                return
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{identity(project)}/provider-batches/{identity(batch_id)}/reconcile",
                json=body,
            ),
            destination,
        )
