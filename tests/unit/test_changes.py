import hashlib
from uuid import uuid4

import pytest

from cutback.changes import CanaryPolicy, canary_decision, model_patch
from cutback.scanner import scan


def test_source_bound_patch_is_reviewed_reversible_and_refuses_stale_input(tmp_path):
    path = tmp_path / "main.py"
    path.write_text(
        "from openai import OpenAI\nc=OpenAI()\n"
        'c.responses.create(model="gpt-4.1", input="synthetic", max_output_tokens=64)\n'
    )
    before = path.read_bytes()
    inventory = scan(tmp_path)
    recommendation = {
        "id": str(uuid4()),
        "run_id": str(uuid4()),
        "status": "accepted_for_staging",
        "evidence": {
            "evaluator_version": "deterministic-v3",
            "lineage": {"contract": "cutback.lineage.v1", "fresh_test_claim": True},
            "quality_gate_passed": True,
            "cheaper_on_sample": True,
            "status": "PROMISING",
            "dataset_hash": "a" * 64,
            "plan_hash": "b" * 64,
            "candidate": {"provider": "openai", "model": "gpt-4.1-mini"},
            "rollback": {"baseline": {"provider": "openai", "model": "gpt-4.1"}},
        },
    }
    from cutback.core.request_profiles import prepare_profile

    profile = prepare_profile(tmp_path, inventory["call_sites"][0])
    recommendation["evidence"]["source_binding"] = profile.binding.model_dump(mode="json")
    recommendation["evidence"]["rollback"]["baseline"] = profile.baseline()
    recommendation["evidence"]["candidate"] = {
        **profile.baseline(),
        "key": "compact",
        "model": "gpt-4.1-mini",
    }
    result = model_patch(
        tmp_path,
        inventory,
        inventory["call_sites"][0]["call_site_id"],
        recommendation,
        expected_source_sha256=hashlib.sha256(before).hexdigest(),
    )
    assert result["status"] == "DRY_RUN_ONLY" and not result["production_deployed"]
    assert "+c.responses.create(model='gpt-4.1-mini'" in result["diff"]
    assert '+c.responses.create(model="gpt-4.1"' in result["rollback_diff"]
    assert path.read_bytes() == before
    reused = {
        **recommendation,
        "evidence": {**recommendation["evidence"], "lineage": {"fresh_test_claim": False}},
    }
    with pytest.raises(ValueError, match="TEST_LINEAGE_REAPPROVAL_REQUIRED"):
        model_patch(
            tmp_path,
            inventory,
            inventory["call_sites"][0]["call_site_id"],
            reused,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
        )
    legacy = {
        **recommendation,
        "evidence": {**recommendation["evidence"], "evaluator_version": "deterministic-v2"},
    }
    with pytest.raises(ValueError, match="EVALUATOR_REAPPROVAL_REQUIRED"):
        model_patch(
            tmp_path,
            inventory,
            inventory["call_sites"][0]["call_site_id"],
            legacy,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
        )
    path.write_text(path.read_text() + "# changed\n")
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        model_patch(
            tmp_path,
            inventory,
            inventory["call_sites"][0]["call_site_id"],
            recommendation,
            expected_source_sha256=hashlib.sha256(before).hexdigest(),
        )


def test_canary_unknown_quality_and_breach_require_operator_rollback():
    baseline = {"cost_per_request_usd": 1, "latency_p95_ms": 10}
    candidate = {
        "observations": 101,
        "error_rate": 0,
        "schema_failure_rate": 0,
        "cost_per_request_usd": 0.5,
        "latency_p95_ms": 11,
    }
    assert canary_decision(CanaryPolicy(), baseline, candidate)["state"] == "GUARDRAILS_PASSED"
    candidate["error_rate"] = 0.5
    result = canary_decision(CanaryPolicy(), baseline, candidate)
    assert result["state"] == "ROLLBACK_RECOMMENDED" and not result["traffic_changed"]
