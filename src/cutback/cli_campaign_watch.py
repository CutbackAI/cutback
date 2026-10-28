"""Agent observation controls. Optional imports stay behind explicit commands."""

from pathlib import Path

import typer


def register_campaign_watch(campaign, output, guarded):
    group = typer.Typer(
        no_args_is_help=True, help="Prepare and inspect finite metadata-only outcome policies."
    )
    campaign.add_typer(group, name="watch")

    @group.command("prepare")
    @guarded
    def prepare(
        change: str = typer.Option(..., "--change"),
        candidate_release: str = typer.Option(..., "--candidate-release"),
        first_window_end: str = typer.Option(..., "--first-window-end"),
        expires_at: str = typer.Option(..., "--expires-at"),
        target: Path = Path("."),
        window_seconds: int = 3600,
        interval_seconds: int | None = None,
        grace_seconds: int = 300,
        max_windows: int = 30,
        expected_tasks: int | None = None,
        release_linkage_attested: bool = False,
        assumptions: Path | None = None,
        key: str = "release-observation",
        watch_id: str | None = None,
    ):
        """Freeze a campaign/source-bound policy with derived cost lineage. No inference."""
        from cutback.agent_control import connect
        from cutback.campaign_watches import prepare as run
        from cutback.core.workspace import read_private

        if not release_linkage_attested:
            raise ValueError("EXPLICIT_CHANGE_TO_APPLICATION_RELEASE_ATTESTATION_REQUIRED")
        control, _ = connect(target)
        output(
            run(
                target,
                control,
                change_id=change,
                candidate_release=candidate_release,
                first_window_end=first_window_end,
                expires_at=expires_at,
                window_seconds=window_seconds,
                interval_seconds=interval_seconds
                if interval_seconds is not None
                else window_seconds,
                grace_seconds=grace_seconds,
                max_windows=max_windows,
                expected_tasks=expected_tasks,
                release_linkage_attested=True,
                assumptions=read_private(assumptions) if assumptions else {},
                key=key,
                watch_id=watch_id,
            )
        )

    @group.command("approve")
    @guarded
    def approve(
        approval_hash: str = typer.Option(..., "--approval-hash"),
        approval_ref: str = typer.Option(..., "--approval-ref"),
        target: Path = Path("."),
    ):
        """Approve only the exact saved metadata policy; authorize no model calls or traffic."""
        from cutback.agent_control import connect
        from cutback.campaign_watches import approve as run

        control, _ = connect(target)
        output(run(target, control, approval_hash=approval_hash, approval_ref=approval_ref))

    @group.command("status")
    @guarded
    def status(target: Path = Path("."), after: int | None = None):
        """Read the saved policy and newest retained windows, with bounded older-page cursors."""
        from cutback.agent_control import connect
        from cutback.campaign_watches import status as run

        control, _ = connect(target)
        output(run(target, control, after=after))
