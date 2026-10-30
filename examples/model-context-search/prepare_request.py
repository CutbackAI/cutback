"""Inspect the exact producer and prepare a frozen bounded search request offline."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from cutback.joint_context_changes import inspect_producer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("Write the request outside the clean application tree.")
    settings, binding, producer = inspect_producer(args.root, "app.py")
    body = {
        "dataset_id": str(args.dataset),
        "settings": settings.model_dump(mode="json"),
        "configuration_binding": binding.model_dump(mode="json"),
        "producer_binding": producer.model_dump(mode="json"),
        "baseline": {
            "key": "baseline",
            "provider": producer.provider,
            "model": producer.model,
            "max_input_tokens": 20000,
            "max_output_tokens": producer.max_output_tokens,
        },
        "metric": {
            "kind": "exact_label",
            "minimum": 0.9,
            "min_test_samples": 3,
            "min_test_groups": 3,
        },
        "budget": {"max_usd": "1", "max_requests": 200},
        "max_candidates": 5,
        "assumed_usd_per_cpu_hour": "0.01",
        "retain_case_outputs": True,
    }
    with args.output.open("x", encoding="utf-8", newline="") as output:
        json.dump(body, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
