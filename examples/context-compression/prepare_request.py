"""Inspect exact settings and prepare a bounded request. No network or inference."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from cutback.compression_changes import inspect_configuration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--dataset", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("Write the request outside the clean application tree.")
    settings, binding = inspect_configuration(args.root, "compression.json")
    body = {
        "dataset_id": str(args.dataset),
        "settings": settings.model_dump(mode="json"),
        "configuration_binding": binding.model_dump(mode="json"),
        "baseline": {
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_input_tokens": 20000,
            "max_output_tokens": 128,
        },
        "metric": {
            "kind": "exact_label",
            "minimum": 0.9,
            "min_test_samples": 3,
            "min_test_groups": 3,
        },
        "budget": {"max_usd": "1", "max_requests": 200},
        "assumed_usd_per_cpu_hour": "0.01",
        "retain_case_outputs": True,
    }
    with args.output.open("x", encoding="utf-8", newline="") as output:
        json.dump(body, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
