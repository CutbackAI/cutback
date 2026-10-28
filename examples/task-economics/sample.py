"""Run against an explicitly selected local collector; provider calls are intercepted."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from cutback.agent_control import Control
from cutback.demo_economics import seed_task_economics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--endpoint")
    destination.add_argument("--local-target", type=Path)
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    if args.local_target:
        from cutback.local_stack import configured, status

        if status(args.local_target)["status"] != "READY":
            raise ValueError("START_OWNED_LOCAL_COLLECTOR_FIRST")
        _, config = configured(args.local_target)
        endpoint, token = config["endpoint"], config["admin"]
    else:
        endpoint, token = args.endpoint, os.environ["CUTBACK_CONTROL_TOKEN"]
    if urlsplit(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("SYNTHETIC_TASK_SAMPLE_REQUIRES_LOOPBACK_COLLECTOR")
    control = Control(endpoint, token)
    slug = "synthetic-task-economics-" + uuid4().hex[:12]
    project = control("POST", "/v1/projects", {"slug": slug})["id"]
    writer = control("POST", f"/v1/projects/{project}/keys", {"role": "ingest"})["token"]
    with httpx.Client(
        base_url=control.endpoint, trust_env=False, follow_redirects=False, timeout=30
    ) as client:
        try:
            result = seed_task_economics(client, control.endpoint, project, writer)
            print(
                json.dumps(
                    {
                        **result,
                        "project_id": project,
                        "project_slug": slug,
                        "dashboard": control.endpoint + "/dashboard/?project=" + project,
                    },
                    indent=2,
                )
            )
        finally:
            if args.cleanup:
                control("DELETE", f"/v1/projects/{project}?confirm_slug={slug}")


if __name__ == "__main__":
    main()
