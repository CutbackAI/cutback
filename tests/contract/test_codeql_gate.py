import json
import runpy
from pathlib import Path

import pytest

check = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/check_codeql_results.py")
)["check"]


def result():
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "CodeQL"}},
                "results": [],
                "invocations": [{"executionSuccessful": True}],
            }
        ],
    }


def test_generated_clean_analysis_can_pass(tmp_path):
    (tmp_path / "python.sarif").write_text(json.dumps(result()))
    assert check(tmp_path) == {"analysis_runs": 1, "findings": 0}


@pytest.mark.parametrize("failure", ["finding", "missing", "failed_analysis", "wrong_analyzer"])
def test_upload_or_empty_results_cannot_bypass_security_gate(tmp_path, failure):
    body = result()
    if failure == "finding":
        body["runs"][0]["results"] = [{"level": "error", "ruleId": "synthetic-sensitive-storage"}]
    elif failure == "failed_analysis":
        body["runs"][0]["invocations"][0]["executionSuccessful"] = False
    elif failure == "wrong_analyzer":
        body["runs"][0]["tool"]["driver"]["name"] = "not-CodeQL"
    if failure != "missing":
        (tmp_path / "python.sarif").write_text(json.dumps(body))
    with pytest.raises(ValueError):
        check(tmp_path)
