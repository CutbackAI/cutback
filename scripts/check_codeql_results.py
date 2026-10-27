"""Fail closed on generated CodeQL findings; successful upload is not a clean analysis."""

import argparse
import json
from pathlib import Path


def check(directory: Path):
    files = sorted(directory.glob("*.sarif"))
    if not files:
        raise ValueError("CODEQL_RESULTS_MISSING")
    runs, findings = 0, 0
    for path in files:
        if path.is_symlink() or path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("CODEQL_RESULTS_UNSAFE_OR_OVERSIZED")
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("version") != "2.1.0" or not body.get("runs"):
            raise ValueError("CODEQL_RESULTS_INCOMPLETE")
        for run in body["runs"]:
            if run.get("tool", {}).get("driver", {}).get("name") != "CodeQL":
                raise ValueError("CODEQL_ANALYZER_NOT_VERIFIED")
            if any(
                not item.get("executionSuccessful", False) for item in run.get("invocations", [])
            ):
                raise ValueError("CODEQL_ANALYSIS_FAILED")
            results = run.get("results")
            if not isinstance(results, list):
                raise ValueError("CODEQL_RESULTS_INCOMPLETE")
            runs += 1
            findings += len(results)
    if findings:
        raise ValueError("CODEQL_FINDINGS_REQUIRE_REMEDIATION")
    return {"analysis_runs": runs, "findings": findings}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    try:
        print(json.dumps(check(parser.parse_args().directory)))
    except (ValueError, OSError):
        raise SystemExit(
            "CodeQL acceptance failed; inspect the protected analysis results."
        ) from None
