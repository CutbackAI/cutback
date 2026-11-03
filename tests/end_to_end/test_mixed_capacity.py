"""Actual concurrent local service paths and explicit incomplete/failure results."""

import json
import os
from pathlib import Path

import pytest
from support_mixed_capacity import MixedProfile, run_mixed


@pytest.fixture
def api_database_url(request):
    # A supervisor may supply only a newly created owned disposable database.
    # Ordinary pytest runs still use the existing session-owned fixture.
    configured = os.getenv("CUTBACK_TEST_MIXED_OWNED_DATABASE_URL")
    if configured:
        from sqlalchemy.engine import make_url

        url = make_url(configured)
        assert url.host in {"localhost", "127.0.0.1", "::1"}
        from uuid import UUID

        assert url.database.startswith("cutback_mixed_")
        UUID(hex=url.database.removeprefix("cutback_mixed_"))
        return configured
    return request.getfixturevalue("postgres_url")


@pytest.mark.postgres
@pytest.mark.parametrize("mode", ["success", "gateway_outage", "strict_slo", "operation_bound"])
def test_owned_two_tenant_mixed_capacity_preserves_accounting_and_failure_gates(
    api, tmp_path, monkeypatch, mode
):
    # An experiment batch performs native calls and durable pre-call
    # transactions. It must finish inside this window, never only drain.
    profile = MixedProfile(duration_seconds=30 if mode == "success" else 10)
    if mode == "strict_slo":
        profile.http_p95_ms_ceiling = 0.000001
    if mode == "operation_bound":
        profile.max_operations_per_actor = 2
    receipt = run_mixed(api, tmp_path, monkeypatch, profile, mode=mode)
    assert receipt["paid_provider_calls"] == 0 and receipt["production_sla"] is False
    assert api[1]["Authorization"][7:] not in json.dumps(receipt)
    assert "owned synthetic urgent" not in json.dumps(receipt)
    assert "owned-synthetic-capacity-key" not in json.dumps(receipt)
    if mode == "success":
        assert receipt["status"] == "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE", receipt["errors"]
        assert receipt["cleanup_complete"]
        assert receipt["client_peak_concurrent_kinds"] >= 3
        assert set(receipt["native_sdk_fixture_calls"]) == {"worker", "quality", "gateway"}
    else:
        assert receipt["status"] == "FAILED_OR_INCOMPLETE_MIXED_QUALIFICATION"
    if mode == "gateway_outage":
        assert receipt["native_sdk_fixture_calls"].get("gateway", 0) <= 2
    if mode == "operation_bound":
        assert any("OPERATION_BOUND_EXHAUSTED" in key for key in receipt["errors"]), receipt[
            "errors"
        ]


@pytest.mark.postgres
def test_explicit_sustained_mixed_profile(api, tmp_path, monkeypatch):
    configured = os.getenv("CUTBACK_TEST_MIXED_CAPACITY_PROFILE")
    if not configured:
        pytest.skip("explicit sustained mixed profile was not selected")
    profile = MixedProfile.model_validate_json(Path(configured).read_text())
    receipt = run_mixed(api, tmp_path, monkeypatch, profile)
    output = os.getenv("CUTBACK_TEST_MIXED_CAPACITY_RECEIPT")
    if output:
        from cutback.core.workspace import write_private

        write_private(Path(output), receipt, exclusive=True)
    assert receipt["status"] == "PASSED_DECLARED_SYNTHETIC_MIXED_ENVELOPE", receipt["errors"]
