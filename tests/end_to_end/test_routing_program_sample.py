"""Ordinary source-agent APIs through real HTTP, native SDK fixtures and checked rollback."""

import os

import httpx
import pytest
from support_http import owned_collector

from cutback.demo_routing_program import run_sample
from cutback.evals.artifacts import EncryptedFileStore


@pytest.mark.postgres
@pytest.mark.parametrize("simple,unknown", [(False, False), (True, False), (False, True)])
def test_independent_routing_program_sample(api, tenant, tmp_path, monkeypatch, simple, unknown):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(
            base_url=endpoint, trust_env=False, follow_redirects=False, timeout=30
        ) as client,
    ):
        receipt = run_sample(
            api[2],
            client,
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
            simple_controls_pass=simple,
            unknown_usage=unknown,
        )
    assert receipt["native_sdk_calls"] > 0
    assert receipt["synthetic_only"] is True
    assert receipt["production_savings_usd"] is None and receipt["deployment_authorized"] is False
    if unknown:
        assert receipt["status"] == "FAILED"
        assert receipt["uncertain_billing_preserved_no_training_replay"] is True
        assert receipt["phases"] == ["static_validation"]
    else:
        assert receipt["status"] == "COMPLETED"
        assert receipt["phases"] == (
            ["static_validation", "confirmation"]
            if simple
            else ["static_validation", "training", "routed_validation", "confirmation"]
        )
        assert receipt["both_application_trees_checked"] is True
        assert receipt["byte_exact_rollback_checked"] is True
        assert receipt["known_cost_requests"] == receipt["native_sdk_calls"]
