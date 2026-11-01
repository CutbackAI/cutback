"""Performance evidence keeps incomplete trials and timing noise honest."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cutback.core.prompts import fingerprint


def profiler():
    path = Path(__file__).resolve().parents[2] / "scripts/instrumentation_profile.py"
    spec = importlib.util.spec_from_file_location("owned_hook_profiler", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "updates",
    [
        {"samples_per_trial": True},
        {"samples_per_trial": 2001},
        {"queue_capacity": 513},
        {"warmup_per_trial": 1},
        {"median_added_ms_ceiling": float("nan")},
        {"median_added_ms_ceiling": float("inf")},
        {"contract": "unrecognized"},
    ],
)
def test_native_profile_requires_bounded_explicit_envelope(updates):
    with pytest.raises(ValueError):
        profiler().Profile(**updates)


@pytest.mark.parametrize("failure", ["partial", "shutdown", "slow"])
def test_partial_or_failed_native_trials_cannot_pass_on_surviving_samples(failure):
    module = profiler()
    profile = module.Profile(samples_per_trial=20)
    trials = [{"raw_call_latencies_ns": [1_000_000] * 20, "error": None} for _ in range(4)]
    if failure == "partial":
        trials[1]["raw_call_latencies_ns"].pop()
    elif failure == "shutdown":
        trials[1]["error"] = "PROFILE_EXPORT_ACCOUNTING_OR_SHUTDOWN_FAILED"
    else:
        for index in (1, 2):
            trials[index]["raw_call_latencies_ns"] = [7_000_000] * 20
    receipt = module.summarize_case("responses", False, trials, profile)
    assert not receipt["passed"]
    assert receipt["trials_abba"] == trials
    if failure == "partial":
        assert receipt["paired_delta"] is None


def test_native_profile_keeps_signed_noise_and_raw_reproducible_quantiles():
    module = profiler()
    profile = module.Profile(samples_per_trial=20)
    trials = [
        {"raw_call_latencies_ns": [value] * 20, "error": None}
        for value in (2_000_000, 1_000_000, 3_000_000, 2_000_000)
    ]
    receipt = module.summarize_case("responses", False, trials, profile)
    assert receipt["raw_signed_paired_deltas_ns"] == [-1_000_000] * 20 + [1_000_000] * 20
    assert receipt["paired_delta"] == {"p50_ms": -1.0, "p95_ms": 1.0, "negative_samples": 20}
    assert receipt["passed"]


def test_actual_native_profile_cli_isolated_outage_privacy_and_no_overwrite(tmp_path):
    source = Path(__file__).resolve().parents[2]
    profile, receipt = tmp_path / "profile.json", tmp_path / "nested/receipt.json"
    profile.write_text(
        json.dumps(
            {
                "samples_per_trial": 20,
                "warmup_per_trial": 2,
                "queue_capacity": 8,
                "median_added_ms_ceiling": 50.0,
            }
        )
    )
    command = [
        sys.executable,
        str(source / "scripts/instrumentation_profile.py"),
        "--profile",
        str(profile),
        "--receipt",
        str(receipt),
    ]
    env = {
        **os.environ,
        "OPENAI_API_KEY": "PRIVATE_INHERITED_PROFILE_KEY",
        "CUTBACK_ADMIN_TOKEN": "PRIVATE_INHERITED_PROFILE_TOKEN",
    }
    result = subprocess.run(command, capture_output=True, timeout=90, env=env)
    assert result.returncode == 0, "NATIVE_PROFILE_CLOSED_TRANSPORT_TRIAL_FAILED"
    original = receipt.read_bytes()
    document = json.loads(original)
    unsigned = {key: value for key, value in document.items() if key != "receipt_hash"}
    assert document["receipt_hash"] == fingerprint(unsigned)
    assert document["paid_provider_calls"] == 0 and document["production_sla"] is False
    assert len(document["cases"]) == 6
    for case in document["cases"]:
        assert case["passed"] and len(case["raw_signed_paired_deltas_ns"]) == 40
        for trial in case["trials_abba"]:
            assert trial["error"] is None and trial["transport_counts"]["privacy_failures"] == 0
            assert len(trial["raw_call_latencies_ns"]) == 20
            if trial["observed"]:
                counters = trial["exporter_counters"]
                assert counters["observed"] == 22 and counters["queue_depth"] == 0
                assert counters["exported"] + counters["dropped"] == 22
                if case["collector_outage"]:
                    assert counters["exported"] == 0 and counters["export_failed"] > 0
    for secret in ("PRIVATE_INHERITED", "OWNED_PRIVATE_PROFILE", "synthetic-provider-credential"):
        assert secret.encode() not in original + result.stdout + result.stderr
    repeated = subprocess.run(command, capture_output=True, timeout=10, env=env)
    assert repeated.returncode == 1 and receipt.read_bytes() == original
