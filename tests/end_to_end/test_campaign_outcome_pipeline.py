"""Independent native application, ordinary agent preparation and later outcomes."""

import json
import os

import pytest

from cutback.demo_campaign_outcomes import run_sample
from cutback.evals.artifacts import EncryptedFileStore

pytestmark = pytest.mark.postgres


def test_ordinary_campaign_checks_uploaded_diff_and_later_rollback_review(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    result = run_sample(
        app,
        api[0],
        "http://127.0.0.1",
        api[1]["Authorization"][7:],
        tenant[0],
        tenant[1]["Authorization"][7:],
        tmp_path,
    )
    assert result["report"]["status"] == "COMPLETED"
    assert (
        result["report"]["requests"]
        == result["report"]["known_cost_requests"]
        == result["native_calls"]
    )
    assert result["application_check_native_calls"] == 2 and result["paid_provider_calls"] == 0
    assert result["review"]["status"] == "READY_FOR_PR_REVIEW"
    assert result["outcome_review"]["next_action"] == "REVIEW_ROLLBACK"
    evidence = result["outcome_review"]["receipt"]["evidence"]
    assert len(evidence["run_costs"]) == 3 and evidence["change_id"]
    assert evidence["report"]["declared_optimization_total_usd"] is not None
    assert evidence["report"]["production_savings_usd"] is None
    assert api[1]["Authorization"][7:] not in json.dumps(result)
