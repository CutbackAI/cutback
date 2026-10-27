"""Agent-facing durable workflow commands; optional imports remain behind explicit actions."""

import functools
import re
from pathlib import Path
from typing import Annotated, Literal

import typer


def register_agent(app, output, fail):
    group = typer.Typer(
        no_args_is_help=True, help="Prepare and resume one source-bound optimization job."
    )
    app.add_typer(group, name="workflow")

    def guarded(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ImportError:
                fail("WORKFLOW_EXTRA_REQUIRED", "install the documented server/eval/provider extra")
            except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
                # Validation exceptions can embed source, examples and credentials.
                fail(
                    str(exc)
                    if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]{5,120}", str(exc))
                    else "WORKFLOW_ACTION_STOPPED",
                    "inspect workflow status and the documented prerequisites; content omitted",
                )

        return call

    @app.command("optimize")
    @guarded
    def optimize(
        target: Annotated[Path, typer.Argument()] = Path("."),
        workflow: str | None = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ):
        """Discover contracts and examples offline; save a resumable job with next actions."""
        from cutback.agent import analyze

        output(analyze(target, workflow=workflow))

    @group.command("status")
    @guarded
    def status(target: Path = Path(".")):
        """Read the saved job without network or application execution."""
        from cutback.agent import load_session, public_session

        output(public_session(load_session(target)))

    @group.command("prepare")
    @guarded
    def prepare(
        target: Path = Path("."),
        example: Annotated[list[str] | None, typer.Option("--example")] = None,
        rights_basis: Literal["synthetic", "owner_authorized", "licensed"] = "owner_authorized",
        rights_ref: str = typer.Option(..., "--rights-reference"),
        max_usd: str = typer.Option(..., "--max-usd"),
        workflow: str | None = None,
        max_requests: int = 100,
        minimum: float | None = None,
        independent_labels: bool = False,
        representative: bool = False,
        local: bool = False,
        endpoint: str | None = None,
        project: str | None = None,
        mapping: Path | None = None,
    ):
        """Prepare permitted examples and the request in an approved encrypted store."""
        from cutback.agent import prepare as run
        from cutback.agent_control import connect
        from cutback.cli_workflows import read_json

        control, identity = connect(target, local=local, endpoint=endpoint, project=project)
        output(
            run(
                target,
                control,
                identity,
                files=example or [],
                rights_basis=rights_basis,
                rights_ref=rights_ref,
                max_usd=max_usd,
                workflow=workflow,
                max_requests=max_requests,
                minimum=minimum,
                independent_labels=independent_labels,
                representative=representative,
                mapping=read_json(mapping) if mapping else None,
            )
        )

    @group.command("continue")
    @guarded
    def advance(
        target: Path = Path("."),
        plan_hash: str | None = None,
        approval_ref: Annotated[str | None, typer.Option("--approval-reference")] = None,
    ):
        """Approve the exact budget once, then queue/poll the same run; worker stays explicit."""
        from cutback.agent import advance as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(run(target, control, plan_hash=plan_hash, approval_ref=approval_ref))

    @group.command("proposal")
    @guarded
    def proposal(target: Path = Path("."), purpose: str = typer.Option(..., "--purpose-reference")):
        """Read authorized evidence; produce a diff/rollback for review without applying it."""
        from cutback.agent import proposal as run
        from cutback.agent_control import connect

        control, _ = connect(target)
        output(run(target, control, purpose=purpose))

    @group.command("work")
    @guarded
    def work(target: Path = Path(".")):
        """Execute one approved run using local operator BYOK configuration."""
        from cutback.agent import load_session
        from cutback.local_stack import configured, database_url

        state = load_session(target)
        if state["phase"] not in {"QUEUED", "RUNNING"}:
            raise ValueError("APPROVED_RUN_REQUIRED")
        location, config = configured(target)
        if state["endpoint"] != config["endpoint"] or not config["allow_payloads"]:
            raise ValueError("LOCAL_OPERATOR_SCOPE_MISMATCH")
        import base64
        import os

        from cutback.evals.artifacts import EncryptedFileStore
        from cutback.server.db import database
        from cutback.server.worker import run_one

        engine, sessions = database(database_url(location, config))
        old = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
        try:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
            store = EncryptedFileStore(
                location / "encrypted", base64.b64decode(config["payload_key"])
            )
            # The worker selects only this job. Other queued projects are never executed here.
            output(run_one(sessions, store, execution_enabled=True, only_run_id=state["run_id"]))
        finally:
            engine.dispose()
            if old is None:
                os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
            else:
                os.environ["CUTBACK_ALLOW_PAYLOADS"] = old
