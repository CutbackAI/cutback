"""Offline preparation and explicitly authorized local execution, separate from scanning."""

import json
from pathlib import Path
from typing import Annotated

import typer


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4194304:
        raise ValueError("APPLICATION_CHECK_INPUT_UNSAFE_OR_TOO_LARGE")
    return json.loads(path.read_text(encoding="utf-8"))


def register_checks(app, output, fail):
    checks = typer.Typer(
        no_args_is_help=True,
        help="Freeze and run reviewed application checks in independent local workspaces.",
    )
    app.add_typer(checks, name="checks")

    @checks.command("prepare")
    def prepare_checks(
        base_root: Path,
        proposed_root: Path,
        commands: Annotated[Path, typer.Option()],
        destination: Annotated[Path, typer.Option("--output")],
    ):
        from cutback.application_checks import prepare

        try:
            contract = prepare(base_root, proposed_root, read(commands))
        except (ValueError, OSError):
            fail(
                "APPLICATION_CHECK_PREPARATION_FAILED",
                "Use clean independent Git roots and bounded reviewed command definitions.",
            )
        output(contract.model_dump(mode="json"), destination)

    @checks.command("run")
    def run_application_checks(
        contract_file: Path,
        base_root: Path,
        proposed_root: Path,
        destination: Annotated[Path, typer.Option("--output")],
        junit_file: Annotated[Path, typer.Option("--junit")],
        execute_local: bool = False,
    ):
        from cutback.application_checks import ApplicationCheckContract, junit, run_checks

        if not execute_local:
            fail(
                "APPLICATION_CHECK_EXPLICIT_LOCAL_EXECUTION_REQUIRED",
                "Review commands and both source trees, then pass --execute-local "
                "in an isolated development or CI workspace.",
            )
        try:
            contract = ApplicationCheckContract.model_validate(read(contract_file))
            result = run_checks(contract, base_root, proposed_root, execute_local=True)
        except (ValueError, OSError):
            fail(
                "APPLICATION_CHECK_EXECUTION_FAILED",
                "Verify the exact clean source trees, command bounds and runtime. "
                "No check output is retained.",
            )
        output(result, destination)
        junit_file.parent.mkdir(parents=True, exist_ok=True)
        junit_file.write_text(junit(result), encoding="utf-8")
        if not result["all_passed"]:
            raise typer.Exit(1)

    @checks.command("verify")
    def verify_application_checks(
        contract_file: Path, receipt_file: Path, base_root: Path, proposed_root: Path
    ):
        from cutback.application_checks import ApplicationCheckContract, verify

        try:
            result = verify(
                ApplicationCheckContract.model_validate(read(contract_file)),
                read(receipt_file),
                base_root,
                proposed_root,
            )
        except (ValueError, OSError):
            fail(
                "APPLICATION_CHECK_RECEIPT_OR_SOURCE_MISMATCH",
                "Run all declared checks against both exact source trees. "
                "A local receipt is not independent attestation.",
            )
        output(result)
