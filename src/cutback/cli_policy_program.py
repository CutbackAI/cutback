"""Explicit controls for one source-bound, whole-budget improvement program."""

import functools
import re
from pathlib import Path
from typing import Annotated

import typer


def register_policy_program(app, output, fail):
    group = typer.Typer(
        no_args_is_help=True,
        help="Compare native models, instructions, routing and exact cache in one approved budget.",
    )
    app.add_typer(group, name="source-policy")

    def guarded(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ImportError:
                fail("POLICY_PROGRAM_DEPENDENCIES_REQUIRED", "Install the documented eval extra.")
            except (OSError, ValueError, KeyError, TypeError) as error:
                code = str(error)
                upgrade = code in {
                    "POLICY_ACTIVITY_MATCHING_COLLECTOR_REQUIRED",
                    "POLICY_PROGRAM_COLLECTOR_UPGRADE_REQUIRED",
                    "POLICY_PROGRAM_ASYNC_COLLECTOR_UPGRADE_REQUIRED",
                    "POLICY_PROGRAM_CURRENT_ROUTER_COLLECTOR_UPGRADE_REQUIRED",
                    "COLLECTOR_SOURCE_WORKLOAD_ASSESSMENT_UPGRADE_REQUIRED",
                }
                data_action = {
                    "POLICY_DATA_SELECT_EXPLICIT_DATASET_OR_IMPORT_INDEPENDENT_LABELS": (
                        "Run source-policy discover for compatible metadata. Import rights-cleared "
                        "independently labeled cases or pass --dataset UUID; ambiguous versions "
                        "are never chosen by recency."
                    ),
                    "POLICY_DATA_SELECT_EXPLICIT_BASELINE_OR_FREEZE_COMPLETE_TASK_COSTS": (
                        "Run source-policy discover. Freeze a scoped task-cost window with "
                        "declared outcomes, attempt coverage and complete costs, or pass "
                        "--baseline-snapshot UUID. See docs/task-economics.md; missing costs "
                        "are not zero."
                    ),
                    "POLICY_DATA_EXPLICIT_EXISTING_ENVIRONMENT_REQUIRED": (
                        "Select an existing collector environment with --environment for discover "
                        "or --data-environment for prepare."
                    ),
                }.get(code)
                fail(
                    code
                    if type(error) is ValueError and re.fullmatch(r"[A-Z_]{3,120}", code)
                    else "POLICY_PROGRAM_ACTION_STOPPED",
                    "Upgrade the collector and worker to the matching Cutback source revision, "
                    "keep the application SDK pin unchanged, then prepare again. "
                    "No provider call was sent."
                    if upgrade
                    else data_action or "Inspect the same saved program; private content omitted.",
                )

        return call

    @group.command("inspect-activity")
    @guarded
    def inspect_activity(
        producer: Annotated[str, typer.Option("--producer")],
        service: Annotated[str, typer.Option("--service")],
        target: Path = Path("."),
        environment: str = "staging",
        window_minutes: int = 60,
        local: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
    ):
        """Inspect current-source cache activity metadata; no application imports or inference."""
        from cutback.agent_control import connect
        from cutback.agent_policy_activity import inspect_current

        control, project_id = connect(target, local=local, endpoint=endpoint, project=project)
        output(
            inspect_current(
                target,
                control,
                project_id,
                producer_file=producer,
                service_name=service,
                environment=environment,
                window_minutes=window_minutes,
            )
        )

    @group.command("assess-workload")
    @guarded
    def assess_workload(
        producer: Annotated[str, typer.Option("--producer")],
        exact_cache_contract: Annotated[Path, typer.Option("--exact-cache-contract")],
        target: Path = Path("."),
        environment: str = "staging",
        analysis_id: str | None = None,
        local: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
    ):
        """Assess retained source-matched metadata; grant no execution or cache activation."""
        from cutback.agent_control import connect
        from cutback.agent_policy_workload import assess as action
        from cutback.cli_checks import read

        control, project_id = connect(target, local=local, endpoint=endpoint, project=project)
        output(
            action(
                target,
                control,
                project_id,
                producer_file=producer,
                cache_execution=read(exact_cache_contract),
                environment=environment,
                analysis_id=analysis_id,
            )
        )

    @group.command("discover")
    @guarded
    def discover(
        producer: Annotated[str, typer.Option("--producer")],
        target: Path = Path("."),
        environment: str = "staging",
        fixture_transport: bool = False,
        local: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
    ):
        """List bounded compatible data metadata; read no cases and approve no calls."""
        from cutback.agent_control import connect
        from cutback.agent_policy_data import discover as action

        control, project_id = connect(target, local=local, endpoint=endpoint, project=project)
        output(
            action(
                target,
                control,
                project_id,
                producer_file=producer,
                environment=environment,
                fixture=fixture_transport,
            )
        )

    @group.command("prepare")
    @guarded
    def prepare(
        producer: Annotated[str, typer.Option("--producer")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        max_usd: Annotated[str, typer.Option("--max-usd")],
        dataset: Annotated[str | None, typer.Option("--dataset")] = None,
        baseline_snapshot: Annotated[str | None, typer.Option("--baseline-snapshot")] = None,
        target: Path = Path("."),
        max_requests: int = 1000,
        max_wall_seconds: int = 600,
        savings_per_success: str | None = None,
        forecast_successful_tasks: int | None = None,
        minimum: float = 0.9,
        min_test_samples: int = 30,
        min_test_groups: int = 30,
        controls: int = 3,
        generate: bool = True,
        fixture_transport: bool = False,
        exact_cache_contract: Path | None = None,
        workload_environment: str = "staging",
        data_environment: str = "staging",
        local: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
    ):
        """Freeze a preview using permitted data and observed costs; send no provider call."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import prepare as action
        from cutback.cli_checks import read

        control, project_id = connect(target, local=local, endpoint=endpoint, project=project)
        output(
            action(
                target,
                control,
                project_id,
                dataset_id=dataset,
                baseline_snapshot_id=baseline_snapshot,
                producer_file=producer,
                purpose=purpose,
                max_usd=max_usd,
                max_requests=max_requests,
                max_wall_seconds=max_wall_seconds,
                savings_per_success=savings_per_success,
                forecast_successful_tasks=forecast_successful_tasks,
                minimum=minimum,
                min_test_samples=min_test_samples,
                min_test_groups=min_test_groups,
                limit=controls,
                generate=generate,
                fixture_transport=fixture_transport,
                cache_contract=read(exact_cache_contract) if exact_cache_contract else None,
                workload_environment=workload_environment,
                data_environment=data_environment,
            )
        )

    @group.command("status")
    @guarded
    def status(target: Path = Path(".")):
        """Read metadata and cumulative known/unknown cost receipts."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import status as action

        control, _ = connect(target)
        output(action(target, control))

    @group.command("continue")
    @guarded
    def advance(
        program_hash: Annotated[str, typer.Option("--program-hash")],
        approval_ref: Annotated[str, typer.Option("--approval-reference")],
        target: Path = Path("."),
    ):
        """Approve the exact whole-search budget once, then reconcile the same program."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import advance as action

        control, _ = connect(target)
        output(action(target, control, program_hash=program_hash, approval_ref=approval_ref))

    @group.command("run")
    @guarded
    def run(
        target: Path = Path("."),
        max_wait_seconds: int = 300,
        max_polls: int = 300,
        poll_seconds: float = 1,
        local_worker: bool = False,
        max_operations: int = 10,
    ):
        """Wait for a configured collector worker; never approve or replay provider calls."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import run as action

        if local_worker:
            from cutback.policy_program_operator import run_local

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

    @group.command("cancel")
    @guarded
    def cancel(target: Path = Path(".")):
        """Stop further work while retaining unresolved billing reservations."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import current

        control, _ = connect(target)
        saved = current(target, control)
        output(
            control(
                "POST",
                f"/v1/projects/{saved['project_id']}/source-policy-programs/{saved['program_id']}/cancel",
                {},
            )
        )

    @group.command("finish")
    @guarded
    def finish(
        private_workspace: Annotated[Path, typer.Option("--workspace")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        target: Path = Path("."),
        coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
        command_file: Annotated[Path | None, typer.Option("--commands")] = None,
        execute_local: bool = False,
        branch: str = "cutback/source-policy-review",
    ):
        """Prepare private source trees, verify inverse rollback, and explicitly run checks."""
        from cutback.agent_control import connect
        from cutback.agent_policy_handoff import finish as action
        from cutback.cli_checks import read

        control, _ = connect(target)
        result = action(
            target,
            control,
            destination=private_workspace,
            purpose=purpose,
            coverage=read(coverage) if coverage else None,
            commands=read(command_file) if command_file else None,
            execute_local=execute_local,
            branch=branch,
        )
        output(result)
        if result["status"] == "CHECKS_FAILED":
            raise typer.Exit(1)

    @group.command("publish")
    @guarded
    def publish(
        repository: Annotated[str, typer.Option("--repository")],
        head_branch: Annotated[str, typer.Option("--head-branch")],
        purpose: Annotated[str, typer.Option("--purpose-reference")],
        target: Path = Path("."),
        base_branch: str = "main",
        publish: Annotated[bool, typer.Option("--publish")] = False,
    ):
        """Preview the checked policy file; upload a draft only with explicit --publish."""
        from cutback.agent_control import connect
        from cutback.agent_policy_publication import publish_review

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

    @group.command("archive")
    @guarded
    def archive(target: Path = Path(".")):
        """Retire known terminal metadata before a new revision; preserve the audit journal."""
        from cutback.agent_control import connect
        from cutback.agent_policy_program import archive as action

        control, _ = connect(target)
        output(action(target, control))

    @group.command("sample")
    @guarded
    def sample(destination: Path, async_consumer: Annotated[bool, typer.Option("--async")] = False):
        """Copy an independent synthetic policy consumer and offline checks into a new directory."""
        from cutback.sample_app.policy_fixture import fixture

        if destination.exists() or destination.is_symlink():
            raise ValueError("POLICY_SAMPLE_DESTINATION_MUST_BE_NEW")
        destination.mkdir(parents=True)
        for name, value in fixture(async_consumer=async_consumer).items():
            (destination / name).write_text(value, encoding="utf-8", newline="")
        output({"status": "SYNTHETIC_SAMPLE_READY", "provider_calls": 0})
