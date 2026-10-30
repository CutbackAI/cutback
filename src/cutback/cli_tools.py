"""Explicit isolated-tool planning and metadata; no daemon access from the CLI."""

import re
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import typer


def register_tools(app, output, request, fail):
    group = typer.Typer(
        no_args_is_help=True,
        help="Prepare scoped isolated record evaluation and inspect canonical tool receipts.",
    )
    app.add_typer(group, name="tools")

    @group.command("prepare")
    def prepare(
        dataset: UUID,
        contract_file: Path,
        *,
        project: Annotated[UUID, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        provider: Literal["openai", "anthropic"] = "openai",
        baseline: str = "gpt-4.1",
        candidate: str | None = None,
        max_usd: str = "0",
        max_requests: int = 100,
        max_turns: int = 8,
        max_wall_seconds: int = 300,
        retain_case_outputs: bool = False,
    ):
        """Freeze a proposal. This neither approves a budget nor queues execution."""
        from cutback.cli_checks import read
        from cutback.core.isolated_episodes import IsolatedRecordExecution
        from cutback.evals.experiments import ModelSpec

        try:
            contract = IsolatedRecordExecution.model_validate(read(contract_file))
            if contract.project_id != project:
                raise ValueError("SCOPE")
            original = ModelSpec(key="baseline", provider=provider, model=baseline)
            candidates = (
                [
                    ModelSpec(key="candidate", provider=provider, model=candidate).model_dump(
                        mode="json"
                    )
                ]
                if candidate
                else None
            )
        except (OSError, ValueError, TypeError):
            fail(
                "ISOLATED_TOOL_CONTRACT_INVALID",
                "Use the operator's pinned scoped tool contract; content and credentials omitted.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/experiments/draft",
                json={
                    "dataset_id": str(dataset),
                    "baseline": original.model_dump(mode="json"),
                    "candidates": candidates,
                    "isolated_tools": contract.model_dump(mode="json"),
                    "episode_max_turns": max_turns,
                    "max_usd": max_usd,
                    "max_requests": max_requests,
                    "max_wall_seconds": max_wall_seconds,
                    "retain_case_outputs": retain_case_outputs,
                },
            ),
            destination,
        )

    @group.command("sessions")
    def sessions(
        run_id: UUID,
        *,
        project: Annotated[UUID, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        after: UUID | None = None,
        limit: int = 50,
    ):
        """Read metadata, containment and explicit unknown tool costs."""
        output(
            request(
                "GET",
                endpoint,
                f"/v1/projects/{project}/experiments/{run_id}/tool-sessions",
                params={"limit": limit, **({"after": str(after)} if after else {})},
            ),
            destination,
        )

    @group.command("operations")
    def operations(
        run_id: UUID,
        session_id: UUID,
        *,
        project: Annotated[UUID, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        after: int = 0,
        limit: int = 50,
    ):
        """Read content-free operation intents; never replay an operation."""
        output(
            request(
                "GET",
                endpoint,
                f"/v1/projects/{project}/experiments/{run_id}/tool-sessions/{session_id}/operations",
                params={"after": after, "limit": limit},
            ),
            destination,
        )

    @group.command("contain")
    def contain(
        run_id: UUID,
        session_id: UUID,
        *,
        project: Annotated[UUID, typer.Option("--project")],
        endpoint: Annotated[str, typer.Option("--endpoint")],
        destination: Annotated[Path, typer.Option("--output")],
        approval_hash: Annotated[str, typer.Option("--approval-hash")],
    ):
        """Queue scoped cleanup; a configured separate worker must confirm it."""
        if not re.fullmatch(r"[a-f0-9]{64}", approval_hash):
            fail(
                "ISOLATED_TOOL_EXACT_APPROVAL_REQUIRED",
                "Copy the exact plan approval from the session receipt.",
            )
        output(
            request(
                "POST",
                endpoint,
                f"/v1/projects/{project}/experiments/{run_id}/tool-sessions/{session_id}/containment",
                json={"approval_hash": approval_hash},
            ),
            destination,
        )
