"""Explicit bounded workload analysis; ordinary scan and observation stay offline."""

from pathlib import Path
from uuid import UUID

import typer


def register_native_profiles(app, output, request, fail):
    group = typer.Typer(no_args_is_help=True, help="Analyze authorized encrypted request repeats.")
    app.add_typer(group, name="request-profiles")

    def base(project):
        return f"/v1/projects/{project}/native-request-profiles/analyses"

    @group.command("analyze")
    def analyze(project: UUID, configuration: Path, endpoint: str = "http://127.0.0.1:8080"):
        """Queue one analysis using a saved request UUID; reconcile uncertain submissions."""
        from cutback.cli_workflows import read_json

        try:
            body = read_json(configuration)
            if not isinstance(body, dict):
                raise ValueError
            UUID(body["request_id"])
        except (OSError, ValueError, KeyError, TypeError):
            fail(
                "NATIVE_PROFILE_CONFIGURATION_REQUIRED",
                "Use a bounded JSON object and request UUID.",
            )
        output(request("POST", endpoint, base(project), json=body))

    @group.command("list")
    def analyses(project: UUID, endpoint: str = "http://127.0.0.1:8080", offset: int = 0):
        """Read a bounded page of aggregate receipts; fingerprints are never returned."""
        if not 0 <= offset <= 100000:
            fail("NATIVE_PROFILE_PAGE_BOUND", "Use an offset between zero and 100000.")
        output(request("GET", endpoint, base(project) + f"?limit=25&offset={offset}"))

    @group.command("show")
    def show(project: UUID, analysis_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(request("GET", endpoint, base(project) + f"/{analysis_id}"))

    @group.command("cancel")
    def cancel(project: UUID, analysis_id: UUID, endpoint: str = "http://127.0.0.1:8080"):
        output(request("POST", endpoint, base(project) + f"/{analysis_id}/cancel", json={}))

    @group.command("worker-once")
    def worker_once():
        """Run one approved analysis with configured encrypted storage; no inference."""
        import os

        from cutback.evals.artifacts import configured_store
        from cutback.server.db import database
        from cutback.server.native_profile_service import run_native_profile_one

        if os.getenv("CUTBACK_ALLOW_PAYLOADS", "false").lower() != "true":
            output({"status": "PAYLOAD_STORAGE_DISABLED", "provider_calls": 0})
            return
        engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
        store = None
        try:
            store = configured_store()
            output(run_native_profile_one(sessions, store))
        finally:
            try:
                close = getattr(store, "close", None)
                if callable(close):
                    close()
            finally:
                engine.dispose()
