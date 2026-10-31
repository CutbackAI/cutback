"""Recheck retained workload evidence at plan and execution boundaries."""

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Context, Decimal, localcontext
from uuid import UUID

from fastapi import HTTPException

from cutback.evals.experiments import ExperimentPlan
from cutback.server.cache_workload_api import CacheWorkloadInput, assess_cache_workload
from cutback.server.db import ExperimentRunRecord
from cutback.server.native_profile_db import NativeProfileAnalysis


def validate_binding(session, project_id, plan: ExperimentPlan):
    bound = plan.workload_portfolio
    if bound is None:
        return None
    try:
        bound.validate_plan(plan)
        if plan.source_binding is None:
            raise ValueError("source")
        if bound.assessment.project_id != project_id or datetime.now(UTC) >= datetime.fromisoformat(
            bound.assessment.valid_until
        ):
            raise ValueError("scope_or_expiry")
        analysis = session.get(
            NativeProfileAnalysis, bound.assessment.analysis_id, populate_existing=True
        )
        if analysis is None or analysis.project_id != project_id:
            raise ValueError("scope")
        current = assess_cache_workload(
            session,
            analysis,
            CacheWorkloadInput(
                source_binding=plan.source_binding,
                baseline_model=plan.baseline.model,
                cache_execution=bound.cache_execution,
            ),
        )
        if current != bound.assessment:
            raise ValueError("changed")
    except (ValueError, HTTPException):
        raise ValueError("WORKLOAD_PORTFOLIO_CURRENT_SOURCE_WINDOW_REQUIRED") from None
    return current


def economic_receipt(session, project_id, plan: ExperimentPlan, report):
    bound = plan.workload_portfolio
    if bound is None:
        return {}
    amounts = [report.get("known_spend_subtotal_usd")]
    complete = report.get("billing_complete") is True
    if plan.predecessor_run_id:
        prior = session.get(ExperimentRunRecord, UUID(plan.predecessor_run_id))
        complete = complete and bool(
            prior
            and prior.project_id == project_id
            and prior.results.get("billing_complete") is True
        )
        amounts.append(str(prior.spent_usd) if prior and prior.project_id == project_id else None)
    with localcontext(Context(prec=80)):
        known = sum((Decimal(value) for value in amounts if value is not None), Decimal(0))
        net = bound.assessment.modeled_net_native_window_opportunity_usd
        if net is None:
            raise ValueError("WORKLOAD_PORTFOLIO_OPPORTUNITY_UNKNOWN")
        opportunity = Decimal(net)
        cache_hypothesis = any(candidate.exact_cache is not None for candidate in plan.candidates)
        windows = (
            str(int((known / opportunity).to_integral_value(rounding=ROUND_CEILING)))
            if complete and cache_hypothesis
            else None
        )
    return {
        "workload_portfolio": {
            "version": bound.version,
            "assessment_sha256": bound.assessment.assessment_sha256,
            "analysis_id": str(bound.assessment.analysis_id),
            "source_window_attempts": bound.assessment.profiled_attempts,
            "modeled_native_window_opportunity_usd": str(opportunity),
            "opportunity_hypothesis": "qualified_exact_cache_only",
            "payback_applies_to_current_candidates": cache_hypothesis,
            "trial_family_known_spend_subtotal_usd": str(known),
            "trial_family_billing_complete": complete,
            "modeled_source_windows_to_cover_trial": windows,
            "scope": "same_retained_native_window_repetition_assumption_not_production_forecast",
            "full_task_cost_usd": None,
            "review_or_deployment_cost_usd": None,
            "realized_savings_usd": None,
        }
    }
