"""Measured eligibility stays bound to its exact source and cannot grant spending."""

from decimal import Inexact, localcontext
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support_agent import fresh_application
from support_retrieval_application import git
from unit.test_cache_workload_contract import assessment

from cutback.agent import analyze, selected_profile
from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.request_profiles import fingerprint
from cutback.core.workload_portfolio import WorkloadPortfolioBinding
from cutback.evals.experiments import Budget, ExperimentPlan, ModelSpec
from cutback.evals.metrics import MetricSpec
from cutback.server.workload_portfolio import economic_receipt


@pytest.fixture
def plan(tmp_path):
    fresh_application(tmp_path)
    git(tmp_path, "init")
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=Synthetic Test",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-m",
        "Independent source fixture",
    )
    profile = selected_profile(tmp_path, analyze(tmp_path))
    baseline = ModelSpec.model_validate(profile.baseline())
    cache = ExactCacheExecution(
        freshness_ref="owned",
        ttl_seconds=300,
        content_retention_authorized=True,
        immutable_or_safely_invalidated=True,
        operation_cost_upper_usd="0.001",
        operation_cost_basis_ref="owned-rate",
    )
    receipt = assessment(
        workflow=profile.binding.workflow_id,
        baseline_model=baseline.model,
        source_binding_sha256=fingerprint(profile.binding.model_dump(mode="json")),
        cache_execution_sha256=fingerprint(cache.model_dump(mode="json")),
    )
    binding = WorkloadPortfolioBinding(assessment=receipt, cache_execution=cache)
    return ExperimentPlan(
        workflow=profile.binding.workflow_id,
        dataset_hash="a" * 64,
        baseline=baseline,
        candidates=[baseline.model_copy(update={"key": "cached", "exact_cache": cache})],
        metric=MetricSpec(kind="exact_label", minimum=1),
        budget=Budget(max_usd="1", max_requests=100),
        approved_providers=["openai"],
        source_binding=profile.binding,
        split="validation",
        workload_portfolio=binding,
    )


def test_bound_portfolio_serialization_preserves_assessment_and_no_execution_authority(plan):
    raw = plan.model_dump(mode="json")
    assert ExperimentPlan.model_validate(raw) == plan
    assert raw["workload_portfolio"]["assessment"]["paid_calls_authorized"] is False
    assert raw["workload_portfolio"]["assessment"]["realized_savings_usd"] is None
    assert "workload_portfolio" not in plan.model_copy(
        update={"workload_portfolio": None}
    ).model_dump(mode="json")


@pytest.mark.parametrize("change", ["cache", "source", "baseline", "missing_cache"])
def test_plan_rejects_changed_qualification_contract(plan, change):
    raw = plan.model_dump(mode="json")
    if change == "cache":
        raw["workload_portfolio"]["cache_execution"]["ttl_seconds"] = 301
    elif change == "source":
        raw["source_binding"]["source_commit"] = "f" * 40
    elif change == "baseline":
        raw["baseline"]["model"] = "gpt-4.1-mini"
    else:
        raw["candidates"][0].pop("exact_cache")
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)


def test_model_only_confirmation_keeps_original_workload_evidence(plan):
    raw = plan.model_dump(mode="json")
    raw.update(split="test", predecessor_run_id=str(uuid4()))
    raw["candidates"][0].pop("exact_cache")
    raw["candidates"][0]["model"] = "gpt-4.1-mini"
    confirmed = ExperimentPlan.model_validate(raw)
    assert confirmed.workload_portfolio == plan.workload_portfolio
    project = uuid4()
    prior = SimpleNamespace(project_id=project, spent_usd="0.1", results={"billing_complete": True})
    result = economic_receipt(
        SimpleNamespace(get=lambda *args: prior),
        project,
        confirmed,
        {"billing_complete": True, "known_spend_subtotal_usd": "0.1"},
    )["workload_portfolio"]
    assert result["opportunity_hypothesis"] == "qualified_exact_cache_only"
    assert result["payback_applies_to_current_candidates"] is False
    assert result["modeled_source_windows_to_cover_trial"] is None


def test_trial_family_cost_counts_validation_and_preserves_unknowns_and_decimal_context(plan):
    project = uuid4()
    confirmed = plan.model_copy(update={"predecessor_run_id": str(uuid4())})
    prior = SimpleNamespace(
        project_id=project, spent_usd="0.000000000001", results={"billing_complete": True}
    )
    session = SimpleNamespace(get=lambda *args: prior)
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.traps[Inexact] = True
        result = economic_receipt(
            session,
            project,
            confirmed,
            {"billing_complete": True, "known_spend_subtotal_usd": "1000.999999999999"},
        )["workload_portfolio"]
        assert context.prec == 2 and context.Emax == 2 and context.traps[Inexact]
    assert result["trial_family_known_spend_subtotal_usd"] == "1001.000000000000"
    assert int(result["modeled_source_windows_to_cover_trial"]) > 0
    assert result["full_task_cost_usd"] is result["realized_savings_usd"] is None
    prior.results["billing_complete"] = False
    assert (
        economic_receipt(
            session,
            project,
            confirmed,
            {"billing_complete": True, "known_spend_subtotal_usd": "0.01"},
        )["workload_portfolio"]["modeled_source_windows_to_cover_trial"]
        is None
    )


def test_window_repetition_count_is_exact_beyond_the_browser_integer_limit(plan):
    raw = plan.model_dump(mode="json")
    original = raw["workload_portfolio"]["assessment"]
    revised = {key: value for key, value in original.items() if key != "assessment_sha256"}
    revised.update(
        modeled_avoided_provider_cost_usd="0.003000000001",
        modeled_net_native_window_opportunity_usd="0.000000000001",
    )
    raw["workload_portfolio"]["assessment"] = assessment(**revised)
    tiny_window = ExperimentPlan.model_validate(raw)
    result = economic_receipt(
        None,
        uuid4(),
        tiny_window,
        {"billing_complete": True, "known_spend_subtotal_usd": "9999.999999999999"},
    )
    assert (
        result["workload_portfolio"]["modeled_source_windows_to_cover_trial"] == "9999999999999999"
    )
