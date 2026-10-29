"""Prepare owned independent policy questions locally; does not upload anything."""

import argparse
import json
from pathlib import Path
from uuid import UUID

from cutback.evals.datasets import DatasetImport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True, type=UUID)
    parser.add_argument("--workflow", default="synthetic-retrieval-search")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.output.resolve().is_relative_to(root):
        raise ValueError("Write import request outside the clean application")
    cases = [
        json.loads(line) for line in (root / "questions.jsonl").read_text("utf-8").splitlines()
    ]
    value = DatasetImport(
        workflow=args.workflow,
        policy_id=str(args.policy),
        source_type="synthetic_fixture",
        independent_labels=True,
        representative=False,
        provenance_ref="owned-policy-families-v1",
        cases=cases,
    )
    args.output.write_text(value.model_dump_json(indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
