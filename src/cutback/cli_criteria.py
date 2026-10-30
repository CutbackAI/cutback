"""Explicit criteria generation, owner review and frozen evaluation controls."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer


def register_criteria(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True,
        help="Propose training-only checks, review them, validate and confirm.",
    )
    app.add_typer(group, name="criteria")

    def send(project, endpoint, suffix, body, destination, *, method="POST"):
        try:
            project = str(UUID(project))
        except ValueError:
            fail("CRITERIA_PROJECT_INVALID", "Supply an exact project UUID.")
        output(
            request(
                method,
                endpoint,
                f"/v1/projects/{project}/criteria" + suffix,
                **({"json": body} if body is not None else {}),
            ),
            destination,
        )

    def identifier(value):
        try:
            return str(UUID(value))
        except ValueError:
            fail("CRITERIA_VERSION_INVALID", "Supply an exact criteria version UUID.")

    def file_body(path):
        from cutback.cli_checks import read

        try:
            value = read(path)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, OSError):
            fail(
                "CRITERIA_REQUEST_INVALID",
                "Use a bounded JSON request object from the documented criteria contract.",
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
        criteria_id: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Reconcile the current state without queuing or replaying a call."""
        send(project, endpoint, "/" + identifier(criteria_id), None, destination, method="GET")

    @group.command("approve-generation")
    def approve(
        criteria_id: str,
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
            "/" + identifier(criteria_id) + "/approve-generation",
            {"plan_hash": plan_hash, "approval_ref": approval_ref},
            destination,
        )

    @group.command("queue")
    def queue(
        criteria_id: str,
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
            "/" + identifier(criteria_id) + "/runs",
            {"plan_hash": plan_hash},
            destination,
        )

    @group.command("content")
    def content(
        criteria_id: str,
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
            "/" + identifier(criteria_id) + "/content",
            {"purpose": purpose},
            destination,
        )

    @group.command("review")
    def review(
        criteria_id: str,
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
            fail("CRITERIA_REVIEW_DECISION_INVALID", "Select approve or reject.")
        send(
            project,
            endpoint,
            "/" + identifier(criteria_id) + "/review",
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

    @group.command("evaluate")
    def evaluate(
        criteria_id: str,
        request_file: Path,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze validation against the current exact criteria approval. No inference."""
        send(
            project,
            endpoint,
            "/" + identifier(criteria_id) + "/evaluation-draft",
            file_body(request_file),
            destination,
        )

    @group.command("source-evidence")
    def source_evidence(
        criteria_id: str,
        recommendation_id: str,
        purpose: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Admin-only private native request context for a current confirmed source diff."""
        send(
            project,
            endpoint,
            "/"
            + identifier(criteria_id)
            + "/recommendations/"
            + identifier(recommendation_id)
            + "/source-evidence",
            {"purpose": purpose},
            destination,
        )

    @group.command("confirm")
    def confirm(
        criteria_id: str,
        validation_run_id: str,
        candidate: str,
        *,
        project: Annotated[str, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        max_usd: Annotated[str, typer.Option("--max-usd")],
        max_requests: Annotated[int, typer.Option("--max-requests")],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        """Freeze one passing validation candidate against untouched test groups."""
        from cutback.evals.experiments import Budget

        try:
            budget = Budget.model_validate({"max_usd": max_usd, "max_requests": max_requests})
            validation_run_id = str(UUID(validation_run_id))
        except ValueError:
            fail(
                "CRITERIA_CONFIRMATION_INPUT_INVALID",
                "Use an exact validation run and a bounded budget.",
            )
        send(
            project,
            endpoint,
            "/" + identifier(criteria_id) + "/confirmation",
            {
                "validation_run_id": validation_run_id,
                "candidate_key": candidate,
                "budget": budget.model_dump(mode="json"),
            },
            destination,
        )

    @group.command("cancel")
    def cancel(
        criteria_id: str,
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
            "/" + identifier(criteria_id) + "/cancel",
            {"reason": reason},
            destination,
        )
