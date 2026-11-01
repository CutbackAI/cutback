"""Exercise the image's source/cache/preparation seed through owned HTTP on the host."""

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from support_http import owned_collector

import cutback
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_current_cached_sample_observations_freeze_a_real_v2_program_without_optimizer_calls(
    api, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    monkeypatch.setenv("CUTBACK_CACHE_ENABLED", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    root = Path(__file__).parents[2]
    tree = ast.parse((root / "scripts/policy_cached_acceptance.py").read_text("utf-8"))
    seed = ast.literal_eval(tree.body[1].value)
    guard = "assert os.getuid() == 10001"
    assert seed.count(guard) == 1
    # The real image retains its UID guard. This source/installed host test
    # changes only that OS assertion and the owned loopback endpoint.
    seed = seed.replace(guard, "assert os.name in {'nt', 'posix'}")
    admin = api[1]["Authorization"][7:]
    with owned_collector(api[2]) as endpoint:
        assert seed.count('endpoint = "http://127.0.0.1:8080"') == 1
        seed = seed.replace('endpoint = "http://127.0.0.1:8080"', "endpoint = " + repr(endpoint))
        preamble = (
            "import sys; sys.path.insert(0, " + repr(str(Path(cutback.__file__).parents[1])) + ")\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", preamble + seed],
            cwd=tmp_path,
            env={**os.environ, "CUTBACK_ADMIN_TOKEN": admin},
            capture_output=True,
            text=True,
            timeout=180,
        )
    diagnostics = {
        "seed_lines": re.findall(r'File "<string>", line ([0-9]+)', result.stderr),
        "exception_types": re.findall(r"(?m)^([A-Za-z_.]+(?:Error|Exception)):", result.stderr)[
            -2:
        ],
    }
    assert admin not in result.stdout
    assert result.returncode == 0, diagnostics
    receipt = json.loads(result.stdout.strip().splitlines()[-1])
    assert receipt["actual_native_sdk_invocations"] == 2
    assert receipt["prepared_without_inference"] and receipt["paid_provider_calls"] == 0
    assert receipt["runtime_summary"]["states"] == {"hit": 2, "native_miss": 2}
    assert receipt["runtime_summary"]["capture_coverage"] == "unknown"
    response = api[0].get(
        "/v1/projects/"
        + receipt["project_id"]
        + "/source-policy-programs/"
        + receipt["program_id"],
        headers=api[1],
    )
    assert response.status_code == 200
    assert response.json()["requests"] == 0 and response.json()["status"] == "READY_FOR_APPROVAL"
