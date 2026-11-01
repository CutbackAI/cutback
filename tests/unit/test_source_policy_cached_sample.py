"""A copied cached-current lesson runs offline and keeps repeated input families intact."""

import json
import subprocess
import sys
from collections import Counter

from test_source_policy_cli import command
from typer.testing import CliRunner


def test_independent_cached_sample_has_native_checks_and_inactive_observation(
    tmp_path, monkeypatch
):
    app, output, errors = command()
    root = tmp_path / "owned-cached-policy-lesson"
    result = CliRunner().invoke(app, ["source-policy", "sample", str(root), "--cached-current"])
    assert result.exit_code == 0, result.output
    assert output == [{"status": "SYNTHETIC_SAMPLE_READY", "provider_calls": 0}] and not errors
    policy = json.loads((root / "policy.json").read_text("utf-8"))
    assert policy["cache_execution"] and "scope" not in policy
    cases = [json.loads(line) for line in (root / "tickets.jsonl").read_text("utf-8").splitlines()]
    assert len(cases) == 160 and set(Counter(row["group"] for row in cases).values()) == {2}
    assert len({row["id"] for row in cases}) == 160
    monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", "OWNED_PRIVATE_SCOPE_MUST_NOT_BE_READ")
    checked = subprocess.run(
        [sys.executable, "check_application.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "contract passed" in checked.stdout
    monkeypatch.delenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE")
    observed = subprocess.run(
        [sys.executable, "observe_application.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert observed.returncode == 0, observed.stdout + observed.stderr
    receipt = json.loads(observed.stdout)
    assert receipt["logical_operations"] == receipt["native_sdk_calls"] == 7
    assert receipt["cache_configured"] and receipt["paid_provider_calls"] == 0
    assert receipt["production_hit_rate"] is None and not receipt["real_model_quality_verified"]


def test_async_cache_sample_is_refused_before_creating_any_destination(tmp_path):
    app, output, errors = command()
    root = tmp_path / "unsupported-async-cache"
    result = CliRunner().invoke(
        app, ["source-policy", "sample", str(root), "--async", "--cached-current"]
    )
    assert result.exit_code == 2
    assert errors[0][0] == "POLICY_SOURCE_ASYNC_CACHE_RUNTIME_UNQUALIFIED"
    assert not output and not root.exists()
