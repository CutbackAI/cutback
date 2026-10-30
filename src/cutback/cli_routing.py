"""Explicit routing preparation and consent; importing the scanner needs no SDK/server."""

import re
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer


def register_routing(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Fit train-only native routes, validate and confirm."
    )
    app.add_typer(group, name="routing")

    def identity(value):
        try:
            parsed = UUID(value)
            if not parsed.int:
                raise ValueError
            return str(parsed)
        except ValueError:
            fail("ROUTING_UUID_REQUIRED", "Use the exact scoped project or resource UUID.")

    def body_file(path):
        from cutback.cli_checks import read

        try:
            value = read(path)
            if not isinstance(value, dict):
                raise ValueError
            # Lost nonbillable replies reuse this exact request and identity.
            identifier = UUID(value["request_id"])
            if identifier.version != 4:
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "ROUTING_REQUEST_INVALID",
                "Use a bounded JSON request with a stable UUID4 request_id.",
            )

    def send(project, endpoint, suffix, body, destination, *, method="POST", private=False):
        result = request(
            method,
            endpoint,
            f"/v1/projects/{identity(project)}" + suffix,
            **({"json": body} if body is not None else {}),
        )
        if private:
            from cutback.core.workspace import write_private_output

            try:
                write_private_output(destination, result)
            except (OSError, ValueError):
                fail(
                    "ROUTING_PRIVATE_OUTPUT_INVALID",
                    "Select a protected regular local output path.",
                )
        else:
            output(result, destination)

    @group.command("training-draft")
    def training_draft(
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze a train-only native comparison. Makes no provider request."""
        send(project, endpoint, "/routing/training-drafts", body_file(request_file), destination)

    @group.command("fit")
    def fit(
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Fit from canonical training receipts. Makes no provider request."""
        send(project, endpoint, "/routing/fits", body_file(request_file), destination)

    @group.command("list")
    def list_fits(
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """List retained fit metadata without private prompts or training outputs."""
        send(project, endpoint, "/routing/fits", None, destination, method="GET")

    @group.command("content")
    def content(
        fit_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Purpose-read the private fitted policy into an explicitly protected file."""
        send(
            project,
            endpoint,
            "/routing/fits/" + identity(fit_id) + "/content",
            {"purpose": purpose},
            destination,
            private=True,
        )

    @group.command("validation-draft")
    def validation(
        fit_id: str,
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze the fitted policy against validation cases under a separate budget."""
        send(
            project,
            endpoint,
            "/routing/fits/" + identity(fit_id) + "/validation-drafts",
            body_file(request_file),
            destination,
        )

    @group.command("confirmation-draft")
    def confirmation(
        fit_id: str,
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze one selected policy for separately authorized untouched confirmation."""
        send(
            project,
            endpoint,
            "/routing/fits/" + identity(fit_id) + "/confirmation-drafts",
            body_file(request_file),
            destination,
        )

    @group.command("approve")
    def approve(
        plan_id: str,
        plan_hash: str,
        approval_reference: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Authorize an exact reviewed training, validation or confirmation plan."""
        if not re.fullmatch(r"[a-f0-9]{64}", plan_hash):
            fail(
                "ROUTING_PLAN_HASH_REQUIRED", "Inspect the exact frozen plan before authorizing it."
            )
        send(
            project,
            endpoint,
            "/experiments/plans/" + identity(plan_id) + "/approve",
            {"plan_hash": plan_hash, "approval_ref": approval_reference},
            destination,
        )

    @group.command("queue")
    def queue(
        plan_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Queue the saved approved run once. This may spend its authorized budget."""
        send(project, endpoint, "/experiments", {"plan_id": identity(plan_id)}, destination)

    @group.command("execution")
    def execution(
        plan_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Reconcile an interrupted queue reply instead of starting another paid run."""
        send(
            project,
            endpoint,
            "/experiments/plans/" + identity(plan_id) + "/execution",
            None,
            destination,
            method="GET",
        )

    @group.command("recheck")
    def recheck(
        run_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Recheck an unstarted blocked run; uncertain billable work cannot be retried."""
        send(project, endpoint, "/experiments/" + identity(run_id) + "/recheck", {}, destination)

    @group.command("result")
    def result(
        run_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Read redacted run evidence, including unknown cost components."""
        send(project, endpoint, "/experiments/" + identity(run_id), None, destination, method="GET")
