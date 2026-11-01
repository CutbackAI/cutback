"""Agent continuation commands; core/HTTP-only installations remain supported."""

from pathlib import Path
from typing import Annotated

import typer


def register_routing_program(group, output, fail):
    import functools
    import re

    commands = typer.Typer(
        no_args_is_help=True, help="Run one approved controls-to-confirmation routing budget."
    )
    group.add_typer(commands, name="program")

    def guarded(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ImportError:
                fail(
                    "ROUTING_PROGRAM_DEPENDENCIES_REQUIRED",
                    "Install HTTPX for a remote collector, or the documented server/provider "
                    "extras for a local worker.",
                )
            except (ValueError, OSError, KeyError, TypeError) as exc:
                code = str(exc)
                fail(
                    code
                    if type(exc) is ValueError and re.fullmatch(r"[A-Z_]{3,120}", code)
                    else "ROUTING_PROGRAM_ACTION_STOPPED",
                    "Inspect the same saved job; content omitted.",
                )

        return call

    @commands.command("prepare")
    @guarded
    def prepare(
        target: Path = Path("."),
        baseline_snapshot: str = typer.Option(..., "--baseline-snapshot"),
        max_usd: str = typer.Option(..., "--max-usd"),
        max_requests: int = 1000,
        max_wall_seconds: int = 600,
        savings_per_success: str | None = None,
        forecast_successful_tasks: int | None = None,
    ):
        """Use the prepared source template and observed costs. Makes no native request."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import prepare as action

        control, _ = connect(target)
        output(
            action(
                target,
                control,
                baseline_snapshot_id=baseline_snapshot,
                max_usd=max_usd,
                max_requests=max_requests,
                max_wall_seconds=max_wall_seconds,
                savings_per_success=savings_per_success,
                forecast_successful_tasks=forecast_successful_tasks,
            )
        )

    @commands.command("status")
    @guarded
    def status(target: Path = Path(".")):
        """Read the same scoped program and cumulative receipts."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import status as action

        control, _ = connect(target)
        output(action(target, control))

    @commands.command("continue")
    @guarded
    def advance(
        target: Path = Path("."),
        program_hash: str | None = None,
        approval_ref: str | None = typer.Option(None, "--approval-reference"),
    ):
        """Approve the exact whole budget once, or reconcile its saved identity."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import advance as action

        control, _ = connect(target)
        output(action(target, control, program_hash=program_hash, approval_ref=approval_ref))

    @commands.command("run")
    @guarded
    def run(
        target: Path = Path("."),
        max_wait_seconds: int = 300,
        max_polls: int = 300,
        poll_seconds: float = 1,
        local_worker: bool = False,
        max_operations: int = 4,
    ):
        """Wait for the existing remote worker. Never approve or replay a call."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import run as action

        if local_worker:
            from cutback.routing_program_operator import run_local

            output(
                run_local(target, max_operations=max_operations, max_wait_seconds=max_wait_seconds)
            )
            return
        control, _ = connect(target)
        output(
            action(
                target,
                control,
                max_wait_seconds=max_wait_seconds,
                max_polls=max_polls,
                poll_seconds=poll_seconds,
            )
        )

    @commands.command("cancel")
    @guarded
    def cancel(target: Path = Path(".")):
        """Stop further native calls; keep unresolved billing reservations."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import current

        control, _ = connect(target)
        saved = current(target, control)
        output(
            control(
                "POST",
                f"/v1/projects/{saved['project_id']}/routing/programs/{saved['program_id']}/cancel",
                {},
            )
        )

    @commands.command("proposal")
    @guarded
    def proposal(
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
    ):
        """Prepare a reversible exact configuration diff into a protected private file."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import proposal as action
        from cutback.core.workspace import write_private_output

        if destination.resolve().is_relative_to(target.resolve()):
            raise ValueError("ROUTING_PRIVATE_OUTPUT_MUST_BE_OUTSIDE_APPLICATION")
        control, _ = connect(target)
        artifact = action(target, control, purpose=purpose)
        write_private_output(destination, artifact)
        output({"status": "PROPOSAL_READY", "applied": False, "deployment_authorized": False})

    @commands.command("review")
    @guarded
    def review(
        contract_file: Path,
        receipt_file: Path,
        proposed_root: Path,
        coverage: Annotated[Path, typer.Option("--coverage")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        destination: Annotated[Path, typer.Option("--output")],
        target: Path = Path("."),
        branch: str | None = None,
    ):
        """Refresh confirmation and verify both checked trees and policy consumers."""
        from cutback.agent_control import connect
        from cutback.agent_routing_program import review as action
        from cutback.cli_checks import read
        from cutback.core.workspace import write_private_output

        if any(
            destination.resolve().is_relative_to(root.resolve()) for root in (target, proposed_root)
        ):
            raise ValueError("ROUTING_PRIVATE_OUTPUT_MUST_BE_OUTSIDE_APPLICATION")
        control, _ = connect(target)
        result = action(
            target,
            control,
            purpose=purpose,
            contract=read(contract_file),
            receipt=read(receipt_file),
            proposed_root=proposed_root,
            coverage=read(coverage),
            branch=branch,
        )
        write_private_output(destination, result)
        output({"status": "LOCAL_REVIEW_READY", "deployment_authorized": False})
