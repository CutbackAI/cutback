"""Paired owned-local trials; actual service timings, never a production SLA."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from cutback.core.workspace import private_directory, read_private, write_private
from cutback.load_qualification import LocalLoadProfile, latency_summary, qualify_local
from cutback.local_stack import configured, status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before-target", type=Path, required=True)
    parser.add_argument("--after-target", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path(__file__).with_name("profile.json"))
    parser.add_argument("--execute-local", action="store_true")
    args = parser.parse_args()
    if not args.execute_local:
        raise ValueError("EXPLICIT_OWNED_LOCAL_TRIAL_EXECUTION_REQUIRED")
    targets = {"before": args.before_target, "after": args.after_target}
    configs = {}
    for label, target in targets.items():
        _, config = configured(target)
        if status(target)["status"] != "READY":
            raise ValueError("BOTH_OWNED_LOCAL_COLLECTORS_MUST_BE_READY")
        configs[label] = config
    if configs["before"]["endpoint"] == configs["after"]["endpoint"]:
        raise ValueError("INDEPENDENT_BEFORE_AND_AFTER_COLLECTORS_REQUIRED")
    profile = LocalLoadProfile.model_validate(read_private(args.profile))
    runs = []
    order = ["before", "after", "after", "before"]
    destination = private_directory(args.after_target, ".cutback/qualification") / (
        "paired-" + uuid4().hex + ".json"
    )
    for label in order:
        config = configs[label]
        receipt = asyncio.run(qualify_local(config["endpoint"], config["admin"], profile))
        runs.append({"label": label, "receipt": receipt})
        write_private(
            destination,
            {
                "planned_order": order,
                "completed_trials": runs,
                "status": "IN_PROGRESS_OR_INTERRUPTED",
            },
        )
        if not receipt["cleanup_complete"]:
            break
    summaries = {}
    for label in targets:
        receipts = [row["receipt"] for row in runs if row["label"] == label]
        if not receipts:
            summaries[label] = {
                "all_declared_gates_passed": False,
                "unique_events_per_mixed_ingest_second": None,
                "latency": {},
            }
            continue
        summaries[label] = {
            "all_declared_gates_passed": all(
                r["status"] == "PASSED_DECLARED_LOCAL_ENVELOPE" for r in receipts
            ),
            "unique_events_per_mixed_ingest_second": (
                sum(r["unique_events_acknowledged"] for r in receipts)
                / (sum(r["mixed_ingest_workload_ns"] for r in receipts) / 1000000000)
                if all(
                    r["mixed_ingest_workload_ns"]
                    and r["aggregate_counts_and_unknown_cost_verified"]
                    for r in receipts
                )
                else None
            ),
            "latency": {
                name: latency_summary(
                    [
                        value
                        for r in receipts
                        for value in r["raw_client_request_latencies_ns"][name]
                    ],
                    minimum=profile.minimum_latency_samples,
                )
                for name in receipts[0]["raw_client_request_latencies_ns"]
            },
        }
    value = {
        "contract": "cutback.paired-local-load-trial.v1",
        "run_order": order,
        "completed_order": [row["label"] for row in runs],
        "all_planned_trials_completed": len(runs) == len(order),
        "profile": profile.model_dump(mode="json"),
        "summaries": summaries,
        "runs": runs,
        "provider_calls": 0,
        "production_capacity_claim": False,
        "code_and_hardware_identity": "operator_controlled_not_independently_attested",
        "cache_and_background_load": "not_controlled_or_inferred",
        "scope": "paired_local_diagnostic_not_customer_benefit_or_causal_proof",
    }
    write_private(destination, value)
    print(
        json.dumps(
            {
                "private_receipt": str(destination),
                "summaries": summaries,
                "provider_calls": 0,
                "production_capacity_claim": False,
            },
            indent=2,
        )
    )
    if len(runs) != len(order) or not all(
        summary["all_declared_gates_passed"] for summary in summaries.values()
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
