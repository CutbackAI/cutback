import os
from uuid import UUID

import pytest

from cutback.demo_runtime_guardrails import seed_runtime_guardrails
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.guardrail_db import GuardrailPolicyRow

pytestmark = pytest.mark.postgres


def test_independent_native_gateway_checker_and_postgres_sample_retires_its_authority(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path, os.urandom(32))
    receipt = seed_runtime_guardrails(api[2], api[0], tenant[0], headers=api[1])
    assert receipt["status"] == "PASSED" and receipt["native_fixture_calls"] == 4
    assert receipt["paid_provider_calls"] == 0 and receipt["complete_application_cost_usd"] is None
    assert receipt["outcomes"]["input_deny"]["delivered"] is False
    assert receipt["outcomes"]["split_stream_deny"]["delivered"] is False
    assert "SYNTHETIC_GUARDED_SAMPLE_CANARY" not in str(receipt)
    with api[2].state.sessions() as session:
        row = session.get(GuardrailPolicyRow, UUID(receipt["policy_id"]))
        assert row.state == "DISABLED" and row.checks_used == 9
