"""Independent application completes one search through real HTTP, PG and checked rollback."""

import pytest
from support_http import owned_collector

from cutback.demo_policy_program import run_sample


@pytest.mark.postgres
def test_independent_policy_program_confirms_one_checked_source_change(
    api, tenant, tmp_path, monkeypatch
):
    import os

    from cutback.evals.artifacts import EncryptedFileStore

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with owned_collector(api[2]) as endpoint:
        receipt = run_sample(
            api[2],
            api[0],
            endpoint,
            api[1]["Authorization"][7:],
            tenant[0],
            tenant[1]["Authorization"][7:],
            tmp_path,
        )
    assert receipt["status"] == "COMPLETED" and receipt["paid_provider_calls"] == 0
    assert receipt["native_sdk_calls"] == receipt["known_cost_requests"]
    assert receipt["phases"] == [
        "static_validation",
        "training",
        "routed_validation",
        "confirmation",
    ]
    assert receipt["checked_handoff"]["status"] == "REVIEWED"
    assert receipt["checked_handoff"]["both_application_trees_checked"] is True
    assert receipt["checked_handoff"]["byte_exact_rollback_checked"] is True
    assert receipt["original_application_changed"] is False
    assert receipt["real_model_quality_verified"] is False
    assert receipt["production_savings_usd"] is None
