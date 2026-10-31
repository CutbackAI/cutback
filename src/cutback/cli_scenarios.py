"""Explicit scenario generation, owner review and frozen evaluation controls."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer


def register_scenario(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True,
        help="Generate training-only scenarios, review and import development cases.",
    )
    app.add_typer(group, name="scenarios")

    def workflow_control(endpoint):
        from cutback.core.config import validate_endpoint

        class BoundControl:
            def __init__(self):
                self.endpoint = validate_endpoint(endpoint)

            def __call__(self, method, path, body=None):
                return request(
                    method, self.endpoint, path, **({"json": body} if body is not None else {})
                )

        return BoundControl()

    def workflow_result(destination, action):
        from cutback.core.workspace import prepare_private_output, write_private_output

        try:
            destination = prepare_private_output(destination)
            result = action()
            write_private_output(destination, result)
        except (ValueError, OSError):
            fail(
                "SCENARIO_WORKFLOW_ACTION_UNAVAILABLE",
                "Reconcile the saved workflow, current rights and exact approval. "
                "Private details are omitted.",
            )

    @group.command("prepare-workflow")
    def prepare_workflow(
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
    ):
        """Persist a nonbillable preparation identity before contacting the collector."""
        from cutback.agent_scenarios import prepare

        workflow_result(
            destination,
            lambda: prepare(target, workflow_control(endpoint), project, file_body(request_file)),
        )

    @group.command("continue-workflow")
    def continue_workflow(
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
        plan_hash: str | None = None,
        approval_ref: str | None = None,
    ):
        """Approve the exact generation budget once, then reconcile only the same run."""
        from cutback.agent_scenarios import advance

        workflow_result(
            destination,
            lambda: advance(
                target,
                workflow_control(endpoint),
                project,
                plan_hash=plan_hash,
                approval_ref=approval_ref,
            ),
        )

    @group.command("read-workflow")
    def read_workflow(
        purpose: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
    ):
        """Read the exact completed proposal into an explicitly requested private file."""
        from cutback.agent_scenarios import read

        workflow_result(
            destination, lambda: read(target, workflow_control(endpoint), project, purpose=purpose)
        )

    @group.command("review-workflow")
    def review_workflow(
        version_sha256: str,
        decision: str,
        approval_ref: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
    ):
        """Record the explicit owner decision and import approved development cases once."""
        from cutback.agent_scenarios import review_and_import

        workflow_result(
            destination,
            lambda: review_and_import(
                target,
                workflow_control(endpoint),
                project,
                version_sha256=version_sha256,
                decision=decision,
                approval_ref=approval_ref,
            ),
        )

    @group.command("import")
    def import_scenarios(
        scenario_id: str,
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Import an exact reviewed version as development-only validation data."""
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/imports",
            file_body(request_file),
            destination,
        )

    def send(project, endpoint, suffix, body, destination, *, method="POST"):
        try:
            project = str(UUID(project))
        except ValueError:
            fail("SCENARIO_PROJECT_INVALID", "Supply an exact project UUID.")
        from cutback.core.workspace import prepare_private_output

        try:
            destination = prepare_private_output(destination)
        except (OSError, ValueError):
            fail(
                "SCENARIO_PRIVATE_OUTPUT_INVALID",
                "Select a protected regular local path before contacting the collector.",
            )
        if suffix == "/drafts" or suffix.endswith("/imports"):
            try:
                if UUID(str(body["request_id"])).version != 4:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                fail(
                    "SCENARIO_REQUEST_ID_REQUIRED",
                    "Use a stable UUID4 request_id so lost replies can be reconciled.",
                )
        result = request(
            method,
            endpoint,
            f"/v1/projects/{project}/scenarios" + suffix,
            **({"json": body} if body is not None else {}),
        )
        if suffix.endswith(("/content", "/source-evidence")):
            from cutback.core.workspace import write_private_output

            try:
                write_private_output(destination, result)
            except (OSError, ValueError):
                fail("SCENARIO_PRIVATE_OUTPUT_INVALID", "Select a protected regular local path.")
        else:
            output(result, destination)

    def identifier(value):
        try:
            return str(UUID(value))
        except ValueError:
            fail("SCENARIO_VERSION_INVALID", "Supply an exact scenario version UUID.")

    def file_body(path):
        from cutback.cli_checks import read

        try:
            value = read(path)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, OSError):
            fail(
                "SCENARIO_REQUEST_INVALID",
                "Use a bounded JSON request object from the documented scenario contract.",
            )

    @group.command("draft")
    def draft(
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze training examples and a one-call budget. Sends no provider request."""
        send(project, endpoint, "/drafts", file_body(request_file), destination)

    @group.command("show")
    def show(
        scenario_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Reconcile the current state without queuing or replaying a call."""
        send(project, endpoint, "/" + identifier(scenario_id), None, destination, method="GET")

    @group.command("approve-generation")
    def approve(
        scenario_id: str,
        plan_hash: str,
        approval_ref: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Admin approval of the exact one-call generation budget."""
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/approve-generation",
            {"plan_hash": plan_hash, "approval_ref": approval_ref},
            destination,
        )

    @group.command("queue")
    def queue(
        scenario_id: str,
        plan_hash: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Queue once. Repeated submissions reconcile the same run, including failures."""
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/runs",
            {"plan_hash": plan_hash},
            destination,
        )

    @group.command("content")
    def content(
        scenario_id: str,
        purpose: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Admin-only audited read into a protected local output file."""
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/content",
            {"purpose": purpose},
            destination,
        )

    @group.command("review")
    def review(
        scenario_id: str,
        version_sha256: str,
        decision: str,
        approval_ref: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        expected_review_approval_id: str | None = None,
    ):
        """Approve or reject immutable content; this does not approve target-call budgets."""
        if decision not in {"approve", "reject"}:
            fail("SCENARIO_REVIEW_DECISION_INVALID", "Select approve or reject.")
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/review",
            {
                "version_sha256": version_sha256,
                "decision": decision,
                "approval_ref": approval_ref,
                "expected_review_approval_id": identifier(expected_review_approval_id)
                if expected_review_approval_id
                else None,
            },
            destination,
        )

    @group.command("cancel")
    def cancel(
        scenario_id: str,
        reason: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """End generation authority. Requests already dispatched can remain billable."""
        send(
            project,
            endpoint,
            "/" + identifier(scenario_id) + "/cancel",
            {"reason": reason},
            destination,
        )
