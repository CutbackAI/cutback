"""Use an owned loopback collector and native SDK test transports; no vendor calls."""

import argparse
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from cutback.core.workspace import private_directory
from cutback.demo_campaigns import seed_campaign
from cutback.evals.artifacts import EncryptedFileStore
from cutback.local_stack import configured, database_url, status
from cutback.server.db import database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-target", type=Path, required=True)
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--workload-cache-first",
        action="store_true",
        help="Observe an independent repeat workload, then run its native/cache campaign",
    )
    parser.add_argument(
        "--ticket-router",
        action="store_true",
        help=(
            "Use the independent three-label ticket task with encrypted case evidence; "
            "requires --outcomes"
        ),
    )
    parser.add_argument(
        "--modern-model",
        action="store_true",
        help="Use a Luna application through ordinary agent preparation; requires --outcomes",
    )
    parser.add_argument(
        "--outcomes",
        action="store_true",
        help="Run ordinary agent preparation, application checks and later declared outcomes",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Include one real closed metadata observation window; requires --outcomes",
    )
    args = parser.parse_args()
    if args.workload_cache_first and any(
        (args.outcomes, args.watch, args.modern_model, args.ticket_router)
    ):
        parser.error("--workload-cache-first is a separate independent sample")
    if args.watch and not args.outcomes:
        parser.error("--watch requires --outcomes")
    if args.modern_model and not args.outcomes:
        parser.error("--modern-model requires --outcomes")
    if args.ticket_router and (not args.outcomes or args.modern_model):
        parser.error("--ticket-router requires --outcomes and its qualified legacy native profile")
    location, config = configured(args.local_target)
    if status(args.local_target)["status"] != "READY" or not config["allow_payloads"]:
        raise ValueError("START_OWNED_LOCAL_COLLECTOR_WITH_AUTHORIZED_ENCRYPTED_STORAGE")
    if urlsplit(config["endpoint"]).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("SYNTHETIC_CAMPAIGN_REQUIRES_OWNED_LOOPBACK_COLLECTOR")
    engine, sessions = database(database_url(location, config))
    owned = SimpleNamespace(
        state=SimpleNamespace(
            sessions=sessions,
            artifact_store=EncryptedFileStore(
                location / "encrypted", base64.b64decode(config["payload_key"])
            ),
        )
    )
    workspace = private_directory(args.local_target, ".cutback/campaign-example")
    previous = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
    try:
        with httpx.Client(
            base_url=config["endpoint"],
            headers={"Authorization": "Bearer " + config["admin"]},
            trust_env=False,
            follow_redirects=False,
            timeout=30,
        ) as client:
            slug = "synthetic-campaign-example-" + uuid4().hex[:10]
            created = client.post("/v1/projects", json={"slug": slug})
            created.raise_for_status()
            project = created.json()["id"]
            key = client.post(f"/v1/projects/{project}/keys", json={"role": "ingest"})
            key.raise_for_status()
            if args.workload_cache_first:
                from cutback.demo_workload_campaign import seed_workload_campaign

                result = seed_workload_campaign(
                    owned, client, project, key.json()["token"], workspace
                )
            elif args.outcomes:
                from cutback.demo_campaign_outcomes import run_sample

                result = run_sample(
                    owned,
                    client,
                    config["endpoint"],
                    config["admin"],
                    project,
                    key.json()["token"],
                    workspace,
                    watch=args.watch,
                    modern_model=args.modern_model,
                    ticket_router=args.ticket_router,
                )
            else:
                result = seed_campaign(owned, client, project, key.json()["token"], workspace)
            report = result["report"]
            print(
                json.dumps(
                    {
                        "project_id": project,
                        "campaign_id": result["campaign_id"],
                        "status": report["status"],
                        "native_calls": result["native_calls"],
                        "known_cost_requests": report["known_cost_requests"],
                        "known_modeled_subtotal_usd": report["known_spend_subtotal_usd"],
                        "decision": report["summary"]["decision"],
                        "paid_provider_calls": 0,
                        **(
                            {
                                "observed_native_calls": result["observed_native_calls"],
                                "proposer_calls": 0,
                                "rollback_verified": result["rollback_verified"],
                                "runtime_cache_activated": False,
                            }
                            if args.workload_cache_first
                            else {}
                        ),
                        **(
                            {
                                "scenario": result["scenario"],
                                "reference_labels": result["reference_labels"],
                            }
                            if args.ticket_router
                            else {}
                        ),
                        "dashboard": config["endpoint"] + "/dashboard/?project=" + project,
                        "disclosure": result["disclosure"],
                        **(
                            {"automatic_observation": result["automatic_observation"]}
                            if args.watch
                            else {}
                        ),
                        **(
                            {
                                "outcome_action": result["outcome_review"]["next_action"],
                                "outcome_receipt": result["outcome_review"]["receipt"]["id"],
                                "application_check_native_calls": result[
                                    "application_check_native_calls"
                                ],
                            }
                            if args.outcomes
                            else {}
                        ),
                    },
                    indent=2,
                )
            )
            if args.cleanup:
                deleted = client.delete(f"/v1/projects/{project}", params={"confirm_slug": slug})
                deleted.raise_for_status()
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = previous


if __name__ == "__main__":
    main()
