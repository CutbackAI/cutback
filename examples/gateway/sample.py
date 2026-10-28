"""Run an independent no-key gateway example against an owned local collector."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from cutback.agent_control import Control
from cutback.core.workspace import private_directory, write_private
from cutback.demo_gateway import run_sample
from cutback.local_stack import configured, database_url, status
from cutback.server.db import database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-target", type=Path, required=True)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    location, config = configured(args.local_target)
    if urlsplit(config["endpoint"]).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SYNTHETIC_GATEWAY_REQUIRES_OWNED_LOOPBACK_COLLECTOR")
    if status(args.local_target)["status"] != "READY":
        raise ValueError("START_SOURCE_MATCHED_OWNED_LOCAL_COLLECTOR")
    folder = private_directory(args.local_target, ".cutback/gateway-example/" + uuid4().hex)
    journal = {"phase": "CREATING", "slug": "synthetic-gateway-example-" + uuid4().hex[:10]}
    write_private(folder / "state.json", journal)
    control = Control(config["endpoint"], config["admin"])
    created = control("POST", "/v1/projects", {"slug": journal["slug"]})
    project = created["id"]
    journal.update(phase="RUNNING", project_id=project)
    write_private(folder / "state.json", journal)
    engine, sessions = database(database_url(location, config))
    try:
        owned = SimpleNamespace(state=SimpleNamespace(engine=engine, sessions=sessions))
        report = run_sample(owned, config["endpoint"], config["admin"], project)
        journal.update(
            phase="COMPLETED",
            report=report,
            finished_at=datetime.now(UTC).isoformat(),
            cleanup="NOT_REQUESTED",
        )
        write_private(folder / "state.json", journal)
        if args.cleanup:
            try:
                control("DELETE", f"/v1/projects/{project}?confirm_slug={journal['slug']}")
            except ValueError:
                journal.update(cleanup="UNCONFIRMED")
                write_private(folder / "state.json", journal)
                raise ValueError("SYNTHETIC_GATEWAY_PROJECT_CLEANUP_UNCONFIRMED") from None
            journal.update(cleanup="CONFIRMED")
            write_private(folder / "state.json", journal)
        print(json.dumps({**report, "cleanup": journal["cleanup"]}, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
