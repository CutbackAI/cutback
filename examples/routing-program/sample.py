"""Use only an explicitly selected owned local stack and synthetic native transports."""

import argparse
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from cutback.agent_control import Control
from cutback.core.workspace import private_directory
from cutback.demo_routing_program import run_sample
from cutback.evals.artifacts import EncryptedFileStore
from cutback.local_stack import configured, database_url, status
from cutback.server.db import database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-target", type=Path, required=True)
    parser.add_argument("--simple-pass", action="store_true")
    parser.add_argument("--unknown-billing", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    location, config = configured(args.local_target)
    if status(args.local_target)["status"] != "READY" or not config["allow_payloads"]:
        raise ValueError("START_OWNED_LOCAL_COLLECTOR_WITH_AUTHORIZED_ENCRYPTED_STORAGE")
    if urlsplit(config["endpoint"]).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ROUTING_PROGRAM_SAMPLE_REQUIRES_OWNED_LOOPBACK_COLLECTOR")
    engine, sessions = database(database_url(location, config))
    owned = SimpleNamespace(
        state=SimpleNamespace(
            sessions=sessions,
            artifact_store=EncryptedFileStore(
                location / "encrypted", base64.b64decode(config["payload_key"], validate=True)
            ),
        )
    )
    control = Control(config["endpoint"], config["admin"])
    previous = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    project = slug = None
    try:
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
        if (
            control("GET", "/v1/health/ready").get("routing_program_contract")
            != "cutback.routing-improvement-program.v1"
        ):
            raise ValueError("ROUTING_PROGRAM_COLLECTOR_UPGRADE_REQUIRED")
        slug = "owned-routing-program-" + uuid4().hex[:12]
        project = control("POST", "/v1/projects", {"slug": slug})["id"]
        ingest = control("POST", f"/v1/projects/{project}/keys", {"role": "ingest"})["token"]
        workspace = private_directory(args.local_target, ".cutback/routing-program-example")
        with httpx.Client(
            base_url=config["endpoint"], trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            receipt = run_sample(
                owned,
                client,
                config["endpoint"],
                config["admin"],
                project,
                ingest,
                workspace,
                simple_controls_pass=args.simple_pass,
                unknown_usage=args.unknown_billing,
            )
        print(
            json.dumps(
                {
                    **receipt,
                    "project_id": project,
                    "dashboard": config["endpoint"] + "/dashboard/?project=" + project,
                },
                indent=2,
            )
        )
    finally:
        engine.dispose()
        if args.cleanup and project and slug:
            control("DELETE", f"/v1/projects/{project}?confirm_slug={slug}")
        if previous is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = previous


if __name__ == "__main__":
    main()
