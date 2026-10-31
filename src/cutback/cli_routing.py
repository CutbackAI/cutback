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

    def private_result(destination, value, *roots):
        from cutback.core.workspace import write_private_output

        if any(destination.resolve().is_relative_to(root.resolve()) for root in roots):
            raise ValueError("ROUTING_PRIVATE_OUTPUT_MUST_BE_OUTSIDE_APPLICATION")
        write_private_output(destination, value)

    @group.command("inspect-source")
    def inspect_source(
        root: Path,
        producer_file: str,
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Parse a qualified clean native application. No imports, execution or network."""
        from cutback.routing_changes import inspect_configuration

        try:
            baseline, binding = inspect_configuration(root, producer_file)
            private_result(
                destination,
                {
                    "baseline": baseline.model_dump(mode="json"),
                    "source": binding.model_dump(mode="json"),
                },
                root,
            )
        except (OSError, ValueError):
            fail(
                "ROUTING_SOURCE_INSPECTION_REFUSED",
                "Use the exact qualified producer and sibling baseline routing.json in a clean "
                "Git application; select a private output outside that tree.",
            )

    def current_source_proposal(project, endpoint, fit_id, run_id, purpose):
        from pydantic import TypeAdapter

        from cutback.core.models import Identifier
        from cutback.routing_changes import RoutingSourceProposal

        purpose = TypeAdapter(Identifier).validate_python(purpose)
        return RoutingSourceProposal.model_validate(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{identity(project)}/routing/fits/{identity(fit_id)}/source-proposal",
                json={"purpose": purpose, "confirmation_run_id": identity(run_id)},
            )
        )

    @group.command("source-proposal")
    def source_proposal(
        root: Path,
        fit_id: str,
        confirmation_run_id: str,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Recheck scoped confirmation and prepare a private exact diff and rollback."""
        from cutback.routing_changes import prepare_patch

        try:
            if destination.resolve().is_relative_to(root.resolve()):
                raise ValueError
            proposal = current_source_proposal(
                project, endpoint, fit_id, confirmation_run_id, purpose
            )
            private_result(destination, prepare_patch(root, proposal), root)
        except (ValueError, OSError):
            fail(
                "ROUTING_SOURCE_PROPOSAL_REFUSED",
                "Check current source-bound confirmation and the unchanged clean application. "
                "Write the private diff outside that tree.",
            )

    @group.command("source-review")
    def source_review(
        artifact_file: Path,
        contract_file: Path,
        receipt_file: Path,
        base_root: Path,
        proposed_root: Path,
        fit_id: str,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        coverage: Annotated[Path, typer.Option("--coverage")],
        destination: Annotated[Path, typer.Option("--output")],
        branch: Annotated[str | None, typer.Option("--branch")] = None,
    ):
        """Refresh collector authority, verify both checked trees, optionally create a local ref."""
        from cutback.cli_checks import read
        from cutback.routing_changes import RoutingSourceProposal, review_change

        try:
            if any(
                destination.resolve().is_relative_to(root.resolve())
                for root in (base_root, proposed_root)
            ):
                raise ValueError
            artifact = read(artifact_file)
            recorded = RoutingSourceProposal.model_validate(artifact["proposal"])
            current = current_source_proposal(
                project, endpoint, fit_id, str(recorded.run_id), purpose
            )
            if current != recorded:
                raise ValueError("ROUTING_SOURCE_COLLECTOR_AUTHORITY_CHANGED")
            result = review_change(
                artifact,
                read(contract_file),
                read(receipt_file),
                base_root,
                proposed_root,
                read(coverage),
                branch=branch,
            )
            private_result(destination, result, base_root, proposed_root)
        except (ValueError, OSError, KeyError, TypeError):
            fail(
                "ROUTING_SOURCE_REVIEW_REFUSED",
                "Refresh scoped confirmation; verify the exact two clean trees, actual checks "
                "and declared policy consumers. A local JSON file is not collector authority.",
            )

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
