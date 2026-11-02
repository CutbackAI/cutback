"""Prepare independent Git roots; execution requires its explicit local flag."""

import argparse
import json
from pathlib import Path

from cutback.application_check_discovery import discover
from cutback.application_checks import ApplicationCheckContract, git, run_checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    changes = parser.add_mutually_exclusive_group()
    changes.add_argument(
        "--broken", action="store_true", help="Propose a shared-consumer regression"
    )
    changes.add_argument(
        "--broken-resource", action="store_true", help="Break the shared prompt contract"
    )
    parser.add_argument("--execute-local", action="store_true")
    args = parser.parse_args()
    workspace = args.workspace.absolute()
    if any(p.is_symlink() for p in [workspace, *workspace.parents]) or workspace.exists():
        raise ValueError("USE_A_NEW_NON_SYMLINK_WORKSPACE")
    source = Path(__file__).resolve().parent / "app"
    workspace.mkdir(parents=True)
    for label in ("base", "proposed"):
        destination = workspace / label
        destination.mkdir()
        for path in sorted(source.iterdir()):
            if path.is_file() and not path.is_symlink():
                raw = path.read_text(encoding="utf-8")
                if label == "proposed" and path.name == "shared.py":
                    if args.broken:
                        raw = raw.replace(".lower()", ".upper()")
                    elif not args.broken_resource:
                        raw += "\n# Proposed equivalent change\n"
                if label == "proposed" and path.name == "prompt.txt" and args.broken_resource:
                    raw = raw.replace("case-insensitive", "case-sensitive")
                (destination / path.name).write_text(raw, encoding="utf-8")
        git(destination, "init")
        git(destination, "add", ".")
        git(
            destination,
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "Independent workflow check sample",
        )
    report = discover(workspace / "base", workspace / "proposed", select_affected=True)
    for name, value in (("contract.json", report["contract"]), ("discovery.json", report)):
        (workspace / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    summary = {
        "mode": report["impact"]["mode"],
        "selected_commands": report["impact"]["selected_commands"],
        "omitted_commands": report["impact"]["omitted_commands"],
        "executed": False,
    }
    if args.execute_local:
        receipt = run_checks(
            ApplicationCheckContract.model_validate(report["contract"]),
            workspace / "base",
            workspace / "proposed",
            execute_local=True,
        )
        (workspace / "receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        summary.update(
            {
                "executed": True,
                "all_passed": receipt["all_passed"],
                "results": [
                    {k: row[k] for k in ("phase", "command", "status")}
                    for row in receipt["results"]
                ],
            }
        )
    print(json.dumps(summary, indent=2))
    return 1 if args.execute_local and not receipt["all_passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
