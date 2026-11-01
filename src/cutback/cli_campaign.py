"""Bounded campaign commands; optional runtime imports stay behind explicit actions."""

from pathlib import Path
from typing import Annotated

import typer


def register_campaign(app, output, guarded):
    group = typer.Typer(
        no_args_is_help=True,
        help="Generate, validate and confirm within one approved campaign budget.",
    )
    app.add_typer(group, name="campaign")
    from cutback.cli_campaign_watch import register_campaign_watch

    register_campaign_watch(group, output, guarded)

    @group.command("prepare")
    @guarded
    def prepare(
        target: Path = Path("."),
        baseline_snapshot: str = typer.Option(..., "--baseline-snapshot"),
        max_usd: str = typer.Option(..., "--max-usd"),
        max_requests: int = 500,
        savings_per_success: str | None = None,
        forecast_successful_tasks: int | None = None,
        generator_model: str = "gpt-4.1-mini",
        rounds: int = 1,
        variants_per_round: int = 2,
        generate_first: bool = typer.Option(
            False,
            "--generate-first",
            help="Use the legacy generation-first search for a new campaign.",
        ),
    ):
        """Derive a frozen search from workflow prepare and observed task costs. No inference."""
        from cutback.agent_campaign import prepare as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(
            run(
                target,
                control,
                baseline_snapshot_id=baseline_snapshot,
                max_usd=max_usd,
                max_requests=max_requests,
                savings_per_success=savings_per_success,
                forecast_successful_tasks=forecast_successful_tasks,
                generator_model=generator_model,
                rounds=rounds,
                variants_per_round=variants_per_round,
                simple_first=False if generate_first else None,
            )
        )

    @group.command("status")
    @guarded
    def status(target: Path = Path(".")):
        """Read the same campaign and its canonical costs; never start requests."""
        from cutback.agent_campaign import status as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(run(target, control))

    @group.command("continue")
    @guarded
    def advance(
        target: Path = Path("."),
        campaign_hash: str | None = None,
        approval_ref: Annotated[str | None, typer.Option("--approval-reference")] = None,
    ):
        """Approve the exact campaign once; repeated calls reconcile the same identity."""
        from cutback.agent_campaign import advance as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(run(target, control, campaign_hash=campaign_hash, approval_ref=approval_ref))

    @group.command("cancel")
    @guarded
    def cancel(target: Path = Path(".")):
        """Stop new calls; late billed usage still repairs the canonical receipt."""
        from cutback.agent_campaign import current
        from cutback.agent_control import connect

        control, _ = connect(target)
        state = current(target, control)
        output(
            control(
                "POST",
                f"/v1/projects/{state['project_id']}/campaigns/{state['campaign_id']}/cancel",
                {},
            )
        )

    @group.command("proposal")
    @guarded
    def proposal(
        target: Path = Path("."),
        purpose: str = typer.Option(..., "--purpose-reference"),
        sample_only: bool = False,
    ):
        """Read confirmed versions; prepare an exact diff and rollback without applying."""
        from cutback.agent_campaign import proposal as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(run(target, control, purpose=purpose, sample_only=sample_only))

    @group.command("review")
    @guarded
    def review(
        contract: Annotated[Path, typer.Option("--contract")],
        receipt: Annotated[Path, typer.Option("--receipt")],
        base: Annotated[Path, typer.Option("--base-root")],
        proposed: Annotated[Path, typer.Option("--proposed-root")],
        coverage: Annotated[Path, typer.Option("--workflow-coverage")],
        target: Path = Path("."),
        purpose: str = typer.Option(..., "--purpose-reference"),
        branch: str | None = typer.Option(None, "--local-review-branch"),
        sample_only: bool = False,
    ):
        """Verify exact source/check receipts; optionally create a local review branch."""
        from cutback.agent_campaign import review as run
        from cutback.agent_control import connect
        from cutback.core.workspace import read_private

        control, _ = connect(target)
        output(
            run(
                target,
                control,
                purpose=purpose,
                contract=read_private(contract),
                receipt=read_private(receipt),
                base=base,
                proposed=proposed,
                coverage=read_private(coverage),
                branch=branch,
                sample_only=sample_only,
            )
        )

    @group.command("publish")
    @guarded
    def publish(
        repository: str = typer.Option(..., "--repository", help="Exact GitHub OWNER/REPO."),
        head_branch: str = typer.Option(..., "--head-branch"),
        purpose: str = typer.Option(..., "--purpose-reference"),
        target: Path = Path("."),
        base_branch: str = "main",
        publish: bool = typer.Option(
            False, "--publish", help="Upload the exact checked commit and create a draft PR."
        ),
    ):
        """Preview a refreshed review; publish only with --publish and CUTBACK_GITHUB_TOKEN."""
        from cutback.agent_control import connect
        from cutback.agent_publication import publish_review

        control, _ = connect(target)
        output(
            publish_review(
                target,
                control,
                purpose=purpose,
                repository=repository,
                base_branch=base_branch,
                head_branch=head_branch,
                publish=publish,
            )
        )

    @group.command("observe")
    @guarded
    def observe(
        candidate_snapshot: str = typer.Option(..., "--candidate-snapshot"),
        change: str = typer.Option(..., "--change"),
        target: Path = Path("."),
        release_linkage_attested: bool = False,
        assumptions: Path | None = None,
        observation_id: str | None = None,
        all_optimization_runs_declared_complete: bool = False,
        other_optimization_costs_declared_complete: bool = False,
        forecast_successful_tasks: int | None = None,
        minimum_tasks: int | None = None,
        max_success_rate_loss: str | None = None,
    ):
        """Link a reviewed campaign to later declared task costs; never change traffic."""
        from cutback.agent_control import connect
        from cutback.campaign_outcomes import record_outcomes
        from cutback.core.workspace import read_private

        declared = {
            **(
                {"all_optimization_runs_declared_complete": True}
                if all_optimization_runs_declared_complete
                else {}
            ),
            **(
                {"other_optimization_costs_declared_complete": True}
                if other_optimization_costs_declared_complete
                else {}
            ),
            **(
                {"forecast_successful_tasks": forecast_successful_tasks}
                if forecast_successful_tasks is not None
                else {}
            ),
            **({"min_tasks_per_window": minimum_tasks} if minimum_tasks is not None else {}),
            **(
                {"max_absolute_success_rate_loss": max_success_rate_loss}
                if max_success_rate_loss is not None
                else {}
            ),
        }
        if assumptions is not None and declared:
            raise ValueError("CHOOSE_ASSUMPTIONS_FILE_OR_EXPLICIT_DECLARATION_FLAGS")
        control, _ = connect(target)
        output(
            record_outcomes(
                target,
                control,
                candidate_snapshot_id=candidate_snapshot,
                change_id=change,
                release_linkage_attested=release_linkage_attested,
                assumptions=read_private(assumptions) if assumptions else declared,
                observation_id=observation_id,
            )
        )

    @group.command("work")
    @guarded
    def work(target: Path = Path(".")):
        """One approved local campaign step, using explicit worker BYOK credentials."""
        from cutback.campaign_operator import run_local

        output({"event": "campaign_step", **run_local(target)})

    @group.command("run")
    @guarded
    def run(target: Path = Path("."), max_operations: int = 8):
        """Finish the same approved local campaign within its original limits. Uses worker BYOK."""
        import json

        from cutback.campaign_operator import run_local

        receipt = run_local(
            target,
            complete=True,
            max_operations=max_operations,
            emit=lambda value: typer.echo(
                json.dumps({"event": "campaign_progress", **value}, ensure_ascii=True, default=str),
                err=True,
            ),
        )
        output({"event": "campaign_result", **receipt})
