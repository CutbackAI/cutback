"""Explicit local corpus read; writes a private draft request outside the application."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from cutback.retrieval_changes import inspect_configuration


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.output.resolve().is_relative_to(root):
        raise ValueError("Write draft request outside the clean application tree")
    settings, binding = inspect_configuration(root, "retrieval.json")
    request = {
        "dataset_id": str(args.dataset),
        "baseline": {
            "key": "baseline",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_input_tokens": 32768,
            "max_output_tokens": 128,
        },
        "settings": settings.model_dump(mode="json"),
        "configuration_binding": binding.model_dump(mode="json"),
        "corpus": json.loads((root / "corpus.json").read_text("utf-8")),
        "metric": {
            "kind": "exact_label",
            "minimum": 0.9,
            "max_regression": 0,
            "min_test_samples": 3,
            "min_test_groups": 3,
        },
        "budget": {"max_usd": "1", "max_requests": 200},
        "assumed_usd_per_cpu_hour": "0.01",
        "retain_case_outputs": True,
    }
    args.output.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
