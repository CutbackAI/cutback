"""Create an owned synthetic import body. No network or provider calls."""

import argparse
import json
from pathlib import Path
from uuid import UUID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=UUID, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in (Path(__file__).parent / "questions.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    body = {
        "workflow": "synthetic-joint-model-context",
        "policy_id": str(args.policy),
        "source_type": "synthetic_fixture",
        "provenance_ref": "owned-context-policy-facts-v1",
        "split_policy": "task-aware-v2",
        "independent_labels": True,
        "representative": False,
        "cases": cases,
    }
    with args.output.open("x", encoding="utf-8", newline="") as output:
        json.dump(body, output, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
