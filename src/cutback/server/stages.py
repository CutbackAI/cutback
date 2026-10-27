"""Stage receipts bind the same dataset/models/criteria; a paid crash never retries."""

from uuid import UUID

from cutback.evals.datasets import digest
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan


def stage_contract(plan):
    value = plan.model_dump(mode="json")
    for key in ("name", "stage", "split", "sample_limit", "predecessor_run_id", "budget"):
        value.pop(key, None)
    return digest(value)


def validate_stage(session, store, project_id, plan):
    if not plan.staged or plan.stage == "smoke":
        return
    previous = session.get(ExperimentRunRecord, UUID(plan.predecessor_run_id))
    if not previous or previous.project_id != project_id or previous.status != "COMPLETED":
        raise ValueError("PREVIOUS_STAGE_NOT_COMPLETED")
    row = session.get(ExperimentPlanRecord, previous.plan_id)
    prior = load_plan(store, project_id, row.specification)
    expected = "smoke" if plan.stage == "pilot" else "pilot"
    if prior.stage != expected or not prior.staged or stage_contract(prior) != stage_contract(plan):
        raise ValueError("STAGE_CONTRACT_CHANGED")
    models = previous.results.get("models", [])
    if not models or not previous.results.get("billing_complete"):
        raise ValueError("PREVIOUS_STAGE_ACCOUNTING_INCOMPLETE")
    if any(not item["quality_gate_passed"] for item in previous.results.get("recommendations", [])):
        raise ValueError("PREVIOUS_STAGE_QUALITY_GATE_FAILED")
    for model in models:
        if model["metric_min"] < prior.metric.minimum or any(
            not item.get("sample_gate_passed", item["passed"])
            for item in model.get("criteria", [])
            if item["required"]
        ):
            raise ValueError("PREVIOUS_STAGE_QUALITY_GATE_FAILED")
