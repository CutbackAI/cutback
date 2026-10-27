"""Stage receipts bind the same dataset/models/criteria; a paid crash never retries."""

from uuid import UUID

from sqlalchemy import select

from cutback.evals.datasets import digest
from cutback.server.db import DatasetRecord, ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan


def stage_contract(plan):
    value = plan.model_dump(mode="json")
    for key in ("name", "stage", "split", "sample_limit", "predecessor_run_id", "budget"):
        value.pop(key, None)
    return digest(value)


def validate_confirmation(session, store, project_id, plan, *, run_id=None):
    """Select exactly one validation candidate, then consume untouched test groups once."""
    previous = session.get(ExperimentRunRecord, UUID(plan.predecessor_run_id))
    if not previous or previous.project_id != project_id or previous.status != "COMPLETED":
        raise ValueError("SEARCH_RUN_NOT_COMPLETED")
    row = session.get(ExperimentPlanRecord, previous.plan_id)
    prior = load_plan(store, project_id, row.specification)
    if (
        prior.staged
        or prior.split != "validation"
        or prior.predecessor_run_id
        or plan.split != "test"
        or plan.sample_limit is not None
        or len(plan.candidates) != 1
        or plan.pilot_samples
    ):
        raise ValueError("CONFIRMATION_REQUIRES_UNTOUCHED_TEST_AND_ONE_VALIDATION_CANDIDATE")
    selected = plan.candidates[0].model_dump(mode="json")
    if selected not in [item.model_dump(mode="json") for item in prior.candidates]:
        raise ValueError("SELECTED_SEARCH_CANDIDATE_CHANGED")
    before, after = prior.model_dump(mode="json"), plan.model_dump(mode="json")
    for value in (before, after):
        for key in ("name", "candidates", "split", "sample_limit", "predecessor_run_id", "budget"):
            value.pop(key, None)
    if before != after:
        raise ValueError("CONFIRMATION_CONTRACT_CHANGED")
    decision: dict = next(
        (
            r
            for r in previous.results.get("recommendations", [])
            if r["candidate"]["key"] == plan.candidates[0].key
        ),
        {},
    )
    if (
        not previous.results.get("billing_complete")
        or not decision.get("quality_gate_passed")
        or not decision.get("cheaper_on_sample")
    ):
        raise ValueError("SELECTED_SEARCH_CANDIDATE_DID_NOT_PASS_GATES")
    # Serialize test-group consumption across different plans and concurrent queue calls.
    dataset = session.scalar(
        select(DatasetRecord)
        .where(
            DatasetRecord.project_id == project_id,
            DatasetRecord.immutable_hash == plan.dataset_hash,
        )
        .with_for_update()
    )
    if not dataset or dataset.id != row.dataset_id:
        raise ValueError("CONFIRMATION_DATASET_CHANGED")
    prior_runs = session.execute(
        select(ExperimentRunRecord, ExperimentPlanRecord)
        .join(
            ExperimentPlanRecord,
            ExperimentRunRecord.plan_id == ExperimentPlanRecord.id,
        )
        .where(ExperimentPlanRecord.dataset_id == dataset.id)
    ).all()
    for other_run, other_plan in prior_runs:
        if (
            other_plan.specification["public_specification"]["split"] == "test"
            and other_run.id != run_id
        ):
            raise ValueError("CONFIRMATION_TEST_GROUPS_ALREADY_RESERVED_OR_CONSUMED")


def validate_stage(session, store, project_id, plan, *, run_id=None):
    if not plan.staged and plan.predecessor_run_id:
        validate_confirmation(session, store, project_id, plan, run_id=run_id)
        return
    if plan.split == "test":
        # Ordinary test plans share the same lock so they cannot race a confirmation
        # and expose its holdout while a selected candidate is being confirmed.
        dataset = session.scalar(
            select(DatasetRecord)
            .where(
                DatasetRecord.project_id == project_id,
                DatasetRecord.immutable_hash == plan.dataset_hash,
            )
            .with_for_update()
        )
        if dataset:
            existing = session.execute(
                select(ExperimentRunRecord, ExperimentPlanRecord)
                .join(
                    ExperimentPlanRecord,
                    ExperimentRunRecord.plan_id == ExperimentPlanRecord.id,
                )
                .where(ExperimentPlanRecord.dataset_id == dataset.id)
            ).all()
            if any(
                other_run.id != run_id
                and not other_plan.specification["public_specification"]["staged"]
                and other_plan.specification["public_specification"].get("predecessor_run_id")
                and other_plan.specification["public_specification"]["split"] == "test"
                for other_run, other_plan in existing
            ):
                raise ValueError("CONFIRMATION_TEST_GROUPS_ALREADY_RESERVED_OR_CONSUMED")
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
