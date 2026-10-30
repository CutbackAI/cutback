"""Explicit scoped cache controls and bounded resumable encrypted dataset transport."""

import json
import re
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer


def register_semantic_cache(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Curate and qualify supplied-vector semantic caches."
    )
    app.add_typer(group, name="semantic-cache")

    def identity(value):
        try:
            return str(UUID(value))
        except ValueError:
            fail("SEMANTIC_CACHE_UUID_REQUIRED", "Use the exact scoped project or resource UUID.")

    def read(path, limit):
        try:
            with path.open("rb") as stream:
                blob = stream.read(limit + 1)
            if len(blob) > limit:
                raise ValueError("BOUND")
            value = json.loads(blob)
            if not isinstance(value, dict):
                raise ValueError("OBJECT_REQUIRED")
            return value
        except (OSError, ValueError, UnicodeError):
            fail(
                "SEMANTIC_CACHE_BOUNDED_JSON_OBJECT_REQUIRED",
                "Review the documented JSON format and byte bound.",
            )

    def base(project):
        return f"/v1/projects/{identity(project)}/semantic-cache"

    @group.command("create")
    def create(
        configuration: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Create the reviewed namespace, storage consent and assigned ingest-key contract."""
        output(
            request(
                "POST", endpoint, base(project) + "/namespaces", json=read(configuration, 65536)
            ),
            destination,
        )

    @group.command("list")
    def list_namespaces(
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Read the first bounded page of namespace metadata."""
        output(
            request("GET", endpoint, base(project) + "/namespaces?limit=100&offset=0"), destination
        )

    @group.command("show")
    def show(
        namespace_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        output(
            request("GET", endpoint, base(project) + "/namespaces/" + identity(namespace_id)),
            destination,
        )

    @group.command("upload-dataset")
    def upload_dataset(
        dataset: Path,
        namespace_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        authorize_content_upload: bool = False,
        resume_upload: str | None = None,
        max_finalize_seconds: int = 60,
        max_finalize_attempts: int = 1,
    ):
        """Upload a <=32 MiB DatasetImport JSON in idempotent chunks, then seal it for the worker.

        The output receipt is saved before content is sent. Resume using that upload UUID;
        an uncertain append is not blindly retried. No inference provider is called.
        """
        if not authorize_content_upload:
            fail(
                "SEMANTIC_UPLOAD_EXPLICIT_CONTENT_APPROVAL_REQUIRED",
                "Review rights, retention and storage; pass --authorize-content-upload.",
            )
        from cutback.evals.datasets import DatasetImport, digest

        try:
            body = DatasetImport.model_validate(read(dataset, 33554432))
            if (
                len(body.cases) > 512
                or not 1 <= max_finalize_seconds <= 120
                or not 1 <= max_finalize_attempts <= 2
            ):
                raise ValueError("BOUND")
        except ValueError:
            fail(
                "SEMANTIC_UPLOAD_DATASET_INVALID",
                "Use <=512 validated cases and the documented finalization bounds.",
            )
        cases = [case.model_dump(mode="json") for case in body.cases]
        hashes = digest(cases)
        route = base(project)
        namespace_uuid = identity(namespace_id)
        if resume_upload:
            upload_route = route + "/dataset-uploads/" + identity(resume_upload)
            receipt = request("GET", endpoint, upload_route)
            if (
                receipt["namespace_id"] != namespace_uuid
                or receipt["specification"]["upload"]["expected_cases_sha256"] != hashes
                or receipt["specification"]["upload"]["expected_cases"] != len(cases)
            ):
                fail(
                    "SEMANTIC_UPLOAD_RESUME_CONTENT_OR_SCOPE_MISMATCH",
                    "Use the exact original namespace and dataset file.",
                )
        else:
            receipt = request(
                "POST",
                endpoint,
                route + "/namespaces/" + namespace_uuid + "/dataset-uploads",
                json={
                    "metadata": body.model_dump(mode="json", exclude={"cases"}),
                    "expected_cases": len(cases),
                    "expected_cases_sha256": hashes,
                    "max_finalize_seconds": max_finalize_seconds,
                    "max_finalize_attempts": max_finalize_attempts,
                    "max_bytes": 33554432,
                    "content_upload_approved": True,
                },
            )
            upload_route = route + "/dataset-uploads/" + identity(receipt["id"])
        output(receipt, destination)
        if receipt["state"] in {"FINALIZING", "FINALIZED"}:
            return
        if receipt["state"] != "STAGING":
            fail(
                "SEMANTIC_UPLOAD_NOT_RESUMABLE",
                "Inspect the receipt and prepare a new explicit upload if necessary.",
            )
        offset = receipt["received_cases"]
        while offset < len(cases):
            chunk = cases[offset : offset + 8]
            while len(json.dumps(chunk).encode()) > 900000 and len(chunk) > 1:
                chunk = chunk[:-1]
            if len(json.dumps(chunk).encode()) > 900000:
                fail(
                    "SEMANTIC_UPLOAD_CASE_EXCEEDS_REQUEST_BOUND",
                    "Reduce the case within the capture-policy and collector bounds.",
                )
            receipt = request(
                "POST",
                endpoint,
                upload_route + "/append",
                json={"approval_hash": receipt["approval_hash"], "offset": offset, "cases": chunk},
            )
            if receipt["received_cases"] != offset + len(chunk):
                fail(
                    "SEMANTIC_UPLOAD_UNEXPECTED_CURSOR",
                    "Inspect the stored receipt before resuming.",
                )
            output(receipt, destination)
            offset = receipt["received_cases"]
        output(
            request(
                "POST",
                endpoint,
                upload_route + "/finalize",
                json={"approval_hash": receipt["approval_hash"]},
            ),
            destination,
        )

    @group.command("upload-status")
    def upload_status(
        upload_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        output(
            request("GET", endpoint, base(project) + "/dataset-uploads/" + identity(upload_id)),
            destination,
        )

    @group.command("prepare")
    def prepare(
        namespace_id: str,
        configuration: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze a dataset, threshold grid, quality gates and local processing bound."""
        output(
            request(
                "POST",
                endpoint,
                base(project) + "/namespaces/" + identity(namespace_id) + "/qualifications",
                json=read(configuration, 65536),
            ),
            destination,
        )

    @group.command("qualification")
    def qualification(
        qualification_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        output(
            request(
                "GET", endpoint, base(project) + "/qualifications/" + identity(qualification_id)
            ),
            destination,
        )

    @group.command("activation")
    def activation(
        qualification_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Read current eligibility and the exact report-bound activation hash."""
        output(
            request(
                "GET",
                endpoint,
                base(project) + "/qualifications/" + identity(qualification_id) + "/activation",
            ),
            destination,
        )

    def approval(action):
        def command(
            qualification_id: str,
            *,
            project: Annotated[str, typer.Option("--project")],
            endpoint: Annotated[str, typer.Option("--endpoint")],
            destination: Annotated[Path, typer.Option("--output")],
            approval_hash: Annotated[str, typer.Option("--approval-hash")],
            approval_ref: Annotated[str, typer.Option("--approval-ref")],
        ):
            if not re.fullmatch(r"[a-f0-9]{64}", approval_hash):
                fail(
                    "SEMANTIC_CACHE_EXACT_APPROVAL_HASH_REQUIRED",
                    "Copy the exact reviewed preparation or activation hash.",
                )
            output(
                request(
                    "POST",
                    endpoint,
                    base(project) + "/qualifications/" + identity(qualification_id) + "/" + action,
                    json={"approval_hash": approval_hash, "approval_ref": approval_ref},
                ),
                destination,
            )

        return command

    group.command("approve")(approval("approve"))
    group.command("activate")(approval("activate"))

    @group.command("cancel-upload")
    def cancel_upload(
        upload_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        approval_hash: Annotated[str, typer.Option("--approval-hash")],
    ):
        output(
            request(
                "POST",
                endpoint,
                base(project) + "/dataset-uploads/" + identity(upload_id) + "/cancel",
                json={"approval_hash": approval_hash},
            ),
            destination,
        )

    def serving(action):
        def command(
            content: Path,
            *,
            endpoint: Annotated[str, typer.Option("--endpoint")],
            destination: Annotated[Path, typer.Option("--output")],
            authorize_content_upload: bool = False,
        ):
            if not authorize_content_upload:
                fail(
                    "SEMANTIC_UPLOAD_EXPLICIT_CONTENT_APPROVAL_REQUIRED",
                    "Vectors and text are sensitive content; pass --authorize-content-upload.",
                )
            output(
                request(
                    "POST",
                    endpoint,
                    "/v1/ingest/semantic-cache/" + action,
                    token_env="CUTBACK_INGEST_TOKEN",
                    json=read(content, 900000),
                ),
                destination,
            )

        return command

    group.command("put")(serving("put"))
    group.command("lookup")(serving("lookup"))

    @group.command("revoke")
    def revoke(
        namespace_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        output(
            request(
                "POST",
                endpoint,
                base(project) + "/namespaces/" + identity(namespace_id) + "/revoke",
                json={},
            ),
            destination,
        )
