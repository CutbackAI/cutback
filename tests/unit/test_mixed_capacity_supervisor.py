"""A hung qualification cannot leave its owned child process tree running."""

import os
import sys
import time

import pytest
from support_mixed_capacity import Measurements, MixedProfile
from support_mixed_supervisor import supervisor

from cutback.core.prompts import fingerprint


def test_mixed_supervisor_stops_descendant_after_deadline(tmp_path):
    marker = tmp_path / "owned-heartbeat"
    child = (
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); "
        "exec('while True:\\n p.write_text(str(time.monotonic_ns()))\\n time.sleep(.03)')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]]); "
        "time.sleep(300)"
    )
    before = time.monotonic()
    code, expired = supervisor().bounded_child(
        [sys.executable, "-c", parent, child, str(marker)],
        cwd=tmp_path,
        env=os.environ.copy(),
        seconds=2,
    )
    assert code is None and expired and time.monotonic() - before < 15
    assert marker.exists(), "owned descendant never started"
    last = marker.read_bytes()
    time.sleep(0.2)
    assert marker.read_bytes() == last, "owned descendant survived timeout"


def test_mixed_window_drain_cannot_supply_missing_within_window_samples():
    measurements = Measurements(MixedProfile(minimum_samples=2))
    measurements.start_ns, measurements.end_ns = 0, 10000000
    for kind in measurements.samples:
        measurements.samples[kind] = [
            {"start_ns": 1, "end_ns": 1000, "successful": True},
            {"start_ns": 2, "end_ns": 20000000, "successful": True},
            {"start_ns": 3, "end_ns": 21000000, "successful": True},
            {"start_ns": 4, "end_ns": 22000000, "successful": True},
        ]
    for metric in measurements.report().values():
        assert metric["p95_ms"] is not None
        assert metric["completed_within_window"] == 1
        assert metric["completed_during_drain"] == 3
        assert metric["passed"] is False


def test_mixed_remote_database_is_rejected_before_output_or_network(tmp_path, monkeypatch):
    import json

    profile, output = tmp_path / "profile.json", tmp_path / "new" / "receipt.json"
    profile.write_text(json.dumps({"duration_seconds": 10}))
    monkeypatch.setenv("CUTBACK_TEST_DATABASE_URL", "postgresql://synthetic@example.invalid/base")
    import psycopg

    monkeypatch.setattr(
        psycopg, "connect", lambda **kwargs: pytest.fail("REMOTE_DATABASE_CONTACTED")
    )
    with pytest.raises(ValueError, match="MIXED_REQUIRES_OWNED_LOCAL_POSTGRESQL"):
        supervisor().qualify(profile, output)
    assert not output.parent.exists()


@pytest.mark.parametrize("change", ["profile", "cleanup", "metrics", "tampered"])
def test_mixed_supervisor_refuses_stale_or_partial_self_hashed_receipts(change):
    profile_hash = "a" * 64
    document = {
        "contract": "cutback.owned-mixed-capacity-receipt.v1",
        "profile_hash": profile_hash,
        "paid_provider_calls": 0,
        "production_sla": False,
        "cleanup_complete": True,
        "tenants": 2,
        "status": "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE",
        "metrics": {kind: {"passed": True} for kind in Measurements(MixedProfile()).samples},
    }
    if change == "profile":
        document["profile_hash"] = "b" * 64
    elif change == "cleanup":
        document["cleanup_complete"] = False
    elif change == "metrics":
        document["metrics"].pop("gateway_request")
    document["receipt_hash"] = fingerprint(document)
    if change == "tampered":
        document["tenants"] = 100
    with pytest.raises(ValueError, match="MIXED_CHILD_RECEIPT"):
        supervisor().validate_child_receipt(document, profile_hash)


@pytest.mark.parametrize(
    "changes",
    [
        {"duration_seconds": True},
        {"duration_seconds": 601},
        {"max_operations_per_actor": 5001},
        {"batch_size": 3},
        {"interval_seconds": float("nan")},
        {"http_p95_ms_ceiling": float("inf")},
    ],
)
def test_mixed_profile_rejects_unbounded_or_ambiguous_envelopes(changes):
    with pytest.raises(ValueError):
        MixedProfile(**changes)
