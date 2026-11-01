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

    @checks.command("isolate")
    def isolate_checks(
        contract_file: Path,
        image_id: Annotated[str, typer.Option()],
        destination: Annotated[Path, typer.Option("--output")],
        python_path: str = "/usr/local/bin/python",
        node_path: str = "/usr/local/bin/node",
    ):
        """Bind reviewed checks to a local image without pulling or executing it."""
        from cutback.application_checks import ApplicationCheckContract
        from cutback.application_container import isolate

        try:
            if destination.resolve() == contract_file.resolve():
                raise ValueError("APPLICATION_CONTAINER_KEEP_ORIGINAL_CONTRACT")
            contract = ApplicationCheckContract.model_validate(read(contract_file))
            result = isolate(
                contract,
                {"image_id": image_id, "python_path": python_path, "node_path": node_path},
            )
        except (ValueError, OSError):
            fail(
                "APPLICATION_CONTAINER_CONTRACT_INVALID",
                "Use a reviewed v1 check contract, a sha256 local image ID and a separate output.",
            )
        output(result.model_dump(mode="json"), destination)

    @checks.command("discover")
    def discover_checks(
        base_root: Path,
        proposed_root: Path,
        destination: Annotated[Path, typer.Option("--output")],
        report: Annotated[Path, typer.Option("--report")],
    ):
        """Inspect existing application checks and prepare a contract without executing code."""
        from cutback.application_check_discovery import discover

        try:
            if destination.resolve() == report.resolve() or any(
                path.resolve().is_relative_to(root.resolve())
                for path in (destination, report)
                for root in (base_root, proposed_root)
            ):
                raise ValueError("APPLICATION_CHECK_OUTPUTS_MUST_STAY_OUTSIDE_FROZEN_TREES")
            result = discover(base_root, proposed_root)
        except (ValueError, OSError, SyntaxError, TypeError):
            fail(
                "APPLICATION_CHECK_DISCOVERY_NEEDS_EXPLICIT_REVIEW",
                "Use clean independent Git roots with existing pytest, native node tests, "
                "a guarded check_application.py or explicit pyproject command declarations.",
            )
        output(result["contract"], destination)
        output(result, report)

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
        from cutback.application_checks import junit, run_checks, validate_contract

        if not execute_local:
            fail(
                "APPLICATION_CHECK_EXPLICIT_LOCAL_EXECUTION_REQUIRED",
                "Review commands and both source trees, then pass --execute-local "
                "in an isolated development or CI workspace.",
            )
        try:
            contract = validate_contract(read(contract_file))
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
        from cutback.application_checks import validate_contract, verify

        try:
            result = verify(
                validate_contract(read(contract_file)),
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
