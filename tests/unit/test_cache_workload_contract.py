"""A workload report's hash, exact money and abstentions cannot become execution approval."""

from datetime import UTC, datetime, timedelta
from decimal import localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from cutback.core.cache_workload import CACHE_WORKLOAD_CONTRACT, CacheWorkloadAssessment
from cutback.core.request_profiles import fingerprint


def assessment(**changes):
    value = {
        "version": CACHE_WORKLOAD_CONTRACT,
        "project_id": str(uuid4()),
        "environment": "staging",
        "workflow": "owned-classification",
        "analysis_id": str(uuid4()),
        "analysis_sha256": "a" * 64,
        "source_binding_sha256": "b" * 64,
        "baseline_model": "gpt-4.1",
        "cache_execution_sha256": "c" * 64,
        "scope": "retained_client_reported_source_window",
        "source_lineage": "client_reported_commit_and_callsite_not_independent_attestation",
        "valid_until": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        "metadata_as_of": datetime.now(UTC).isoformat(),
        "decision": "prepare_separate_cache_trial",
        "reasons": [],
        "profiled_attempts": 2,
        "modeled_potential_hits": 1,
        "provider_modeled_window_cost_usd": "0.10",
        "modeled_avoided_provider_cost_usd": "0.05",
        "declared_cache_operations": 3,
        "declared_cache_operation_upper_cost_usd": "0.003",
        "modeled_net_native_window_opportunity_usd": "0.047",
        "production_hit_rate": None,
        "whole_task_cost_usd": None,
        "realized_savings_usd": None,
        "quality_evidence": "separate_independent_trial_required",
        "cache_delivery_benchmarked": False,
        "paid_calls_authorized": False,
        "source_change_authorized": False,
        "provider_calls": 0,
        "encrypted_storage_reads": 0,
        **changes,
    }
    return {**value, "assessment_sha256": fingerprint(value)}


def test_metadata_assessment_round_trip_keeps_hash_and_exact_decimal_text():
    body = assessment()
    result = CacheWorkloadAssessment.model_validate(body)
    assert result.model_dump(mode="json") == body
    assert result.modeled_net_native_window_opportunity_usd == "0.047"


def test_money_ceiling_is_exact_even_when_the_callers_decimal_precision_is_low():
    with localcontext() as context:
        context.prec = 2
        with pytest.raises(ValidationError):
            CacheWorkloadAssessment.model_validate(
                assessment(provider_modeled_window_cost_usd="1000000000000000001")
            )


@pytest.mark.parametrize(
    "field", ["paid_calls_authorized", "source_change_authorized", "cache_delivery_benchmarked"]
)
@pytest.mark.parametrize("value", [True, 0, "false"])
def test_even_correctly_rehashed_reports_cannot_invent_authority(field, value):
    with pytest.raises(ValidationError):
        CacheWorkloadAssessment.model_validate(assessment(**{field: value}))


@pytest.mark.parametrize(
    "changes",
    [
        {"provider_calls": 1},
        {"encrypted_storage_reads": True},
        {"whole_task_cost_usd": "0"},
        {"realized_savings_usd": "0"},
        {"production_hit_rate": "0.5"},
        {"modeled_net_native_window_opportunity_usd": "NaN"},
        {"modeled_net_native_window_opportunity_usd": 0.047},
        {"declared_cache_operation_upper_cost_usd": "1e-1000000000"},
        {"modeled_avoided_provider_cost_usd": "0.0000000000001"},
        {"modeled_net_native_window_opportunity_usd": "0.048"},
        {"modeled_potential_hits": 2},
        {"declared_cache_operations": 2},
        {"modeled_avoided_provider_cost_usd": None},
        {"provider_modeled_window_cost_usd": None},
        {"valid_until": "2026-01-01T12:00:00"},
        {"decision": "abstain", "reasons": []},
    ],
)
def test_unknown_conflicting_or_unqualified_evidence_cannot_qualify(changes):
    with pytest.raises(ValidationError):
        CacheWorkloadAssessment.model_validate(assessment(**changes))


def test_modified_receipt_is_rejected_and_explicit_unknown_abstention_is_preserved():
    body = assessment()
    body["baseline_model"] = "changed-model"
    with pytest.raises(ValidationError):
        CacheWorkloadAssessment.model_validate(body)
    unknown = assessment(
        decision="abstain",
        reasons=["SOURCE_COST_UNKNOWN"],
        provider_modeled_window_cost_usd=None,
        modeled_avoided_provider_cost_usd=None,
        declared_cache_operations=None,
        declared_cache_operation_upper_cost_usd=None,
        modeled_net_native_window_opportunity_usd=None,
    )
    assert CacheWorkloadAssessment.model_validate(unknown).model_dump(mode="json") == unknown
