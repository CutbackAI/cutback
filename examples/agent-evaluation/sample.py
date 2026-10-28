"""Run the independent synthetic support-agent and RAG applications."""

import argparse
import json
from pathlib import Path

from cutback.demo_agents import demonstrate, rag_case, support_case

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-fixtures", type=Path)
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="Include scripted semantic judge fixtures; zero vendor calls",
    )
    args = parser.parse_args()
    if args.write_fixtures:
        args.write_fixtures.mkdir(parents=True, exist_ok=True)
        for name, case in [("support", support_case()), ("rag", rag_case())]:
            (args.write_fixtures / (name + "-scenario.json")).write_text(
                json.dumps(case["episode"], indent=2), encoding="utf-8"
            )
    print(json.dumps(demonstrate(semantic=args.semantic), indent=2))
