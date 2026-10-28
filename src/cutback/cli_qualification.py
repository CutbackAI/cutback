"""Owned-local qualification entry points; optional runtime imports stay explicit."""

from pathlib import Path
from typing import Annotated

import typer


def register_qualification(app, output, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Measure declared owned-local service envelopes."
    )
    app.add_typer(group, name="qualify")

    @group.command("load")
    def load(
        profile_file: Annotated[Path, typer.Option("--profile")],
        target: Path = Path("."),
        execute_local: bool = False,
    ):
        """Create/delete independent synthetic tenants and measure actual local HTTP paths."""
        if not execute_local:
            fail(
                "LOAD_EXPLICIT_LOCAL_EXECUTION_REQUIRED",
                "Review the local profile and pass --execute-local.",
            )
        try:
            import asyncio

            from cutback.core.workspace import private_directory, read_private, write_private
            from cutback.load_qualification import LocalLoadProfile, qualify_local
            from cutback.local_stack import configured, status

            _, config = configured(target)
            if status(target)["status"] != "READY":
                raise ValueError("OWNED_LOCAL_COLLECTOR_NOT_READY")
            profile = LocalLoadProfile.model_validate(read_private(profile_file))
            receipt = asyncio.run(qualify_local(config["endpoint"], config["admin"], profile))
            destination = private_directory(target, ".cutback/qualification") / (
                receipt["receipt_id"] + ".json"
            )
            write_private(destination, receipt, exclusive=True)
        except ImportError:
            fail("MISSING_EXTRA", "Use an isolated Cutback server environment.")
        except (ValueError, KeyError, OSError):
            fail(
                "LOCAL_LOAD_QUALIFICATION_FAILED",
                "Verify ownership, profile bounds and local collector health; "
                "sensitive details omitted.",
            )
        output({**receipt, "private_receipt": str(destination)})
        if receipt["status"] != "PASSED_DECLARED_LOCAL_ENVELOPE":
            raise typer.Exit(code=1)
