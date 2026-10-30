"""Reviewed checks retain separate setup economics and do not loosen old gates."""

from copy import deepcopy
from uuid import uuid4

import pytest

from cutback.core.evaluation_criteria import source_evaluator_eligible
from cutback.server.plan_artifacts import public_config


def evidence():
    return {
        "evaluator_version": "deterministic-v13",
        "criteria_binding": {
            "criteria_id": str(uuid4()),
            "review_approval_id": str(uuid4()),
            "version_sha256": "a" * 64,
        },
        "criteria_review_assurance": "owner_review_not_independent_judge_calibration",
        "criteria_confirmation_predecessor_run_id": str(uuid4()),
        "criteria_generation_cost": {
            "generation_run_id": str(uuid4()),
            "known_spend_subtotal_usd": "0.0005",
            "billing_complete": True,
            "shared_one_time_setup": True,
            "included_in_target_evaluation_spend": False,
            "amortized_per_task_usd": None,
            "full_task_economics_known": False,
        },
    }


@pytest.mark.parametrize(
    "change", ["binding", "nonce", "predecessor", "billing", "cost", "amortization", "claim"]
)
def test_only_pinned_confirmation_receipts_extend_the_source_evaluator(change):
    value = evidence()
    assert source_evaluator_eligible(value)
    if change == "binding":
        value.pop("criteria_binding")
    elif change == "nonce":
        value["criteria_binding"]["review_approval_id"] = "00000000-0000-0000-0000-000000000000"
    elif change == "predecessor":
        value["criteria_confirmation_predecessor_run_id"] = None
    elif change == "billing":
        value["criteria_generation_cost"]["billing_complete"] = None
    elif change == "cost":
        value["criteria_generation_cost"]["known_spend_subtotal_usd"] = "NaN"
    elif change == "amortization":
        value["criteria_generation_cost"]["amortized_per_task_usd"] = "0"
    else:
        value["criteria_generation_cost"]["full_task_economics_known"] = True
    assert not source_evaluator_eligible(value)
    assert source_evaluator_eligible({"evaluator_version": "deterministic-v3"})
    assert not source_evaluator_eligible({"evaluator_version": "deterministic-v12"})


def test_metadata_redacts_generated_fields_in_primary_and_required_metrics():
    metric = {
        "kind": "json_schema",
        "minimum": 0.9,
        "output_field": "owned_private_field",
        "schema_definition": {"required": ["owned_private_field"]},
    }
    original = {
        "criteria_binding": evidence()["criteria_binding"],
        "metric": metric,
        "criteria": [{"key": "criterion-1", "metric": deepcopy(metric)}],
    }
    summary = public_config(original)
    assert summary["metric"]["output_field"] is None
    assert summary["criteria"][0]["metric"]["output_field"] is None
    assert summary["criteria"][0]["metric"]["minimum"] == 0.9
    assert "owned_private_field" not in str(summary)
    assert original["metric"]["output_field"] == "owned_private_field"
