"""Frozen batch envelope, current authority and metadata-only result contracts."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from cutback.evals.datasets import digest
from cutback.evals.experiments import plan_summary
from cutback.evals.input_groups import effective_input_groups
from cutback.evals.provider_batch import VERSION, input_file, price_reference, validate_model
from cutback.server.db import DatasetRecord, Environment, ExperimentPlanRecord
from cutback.server.plan_artifacts import load_plan
from cutback.server.principals import principal_role
from cutback.server.worker import load_dataset


def batch_inputs(session, store, project, plan_row, *, deadline=None):
    if not plan_row or plan_row.project_id != project:
        raise ValueError("BATCH_PLAN_NOT_FOUND")
    plan = load_plan(store, project, plan_row.specification)
    if (
        plan.split != "validation"
        or plan.episode_execution
        or plan.retrieval_execution
        or plan.compression_execution
        or plan.source_binding
        or plan.prompt_bindings
        or plan.judge
        or plan.staged
        or plan.pilot_samples
        or plan.retain_case_outputs
        or plan.metric.kind not in {"exact_label", "field_f1", "json_schema", "numeric_match"}
    ):
        raise ValueError("BATCH_REQUIRES_EXPLICIT_PLAIN_VALIDATION_PLAN")
    for target in [plan.baseline, *plan.candidates]:
        validate_model(target)
    dataset = session.get(DatasetRecord, plan_row.dataset_id)
    if not dataset or dataset.project_id != project or dataset.workflow_id != plan.workflow:
        raise ValueError("BATCH_DATASET_SCOPE_MISMATCH")
    loaded, policy = load_dataset(session, dataset, store)
    if "openai" not in policy.allowed_providers:
        raise ValueError("BATCH_PROVIDER_NOT_AUTHORIZED")
    if (
        deadline
        and min(policy.expires_at, datetime.fromisoformat(loaded["expires_at"])) <= deadline
    ):
        raise ValueError("BATCH_CONTENT_EXPIRES_BEFORE_TURNAROUND")
    environment = session.scalar(
        select(Environment).where(
            Environment.project_id == project, Environment.name == dataset.provenance["environment"]
        )
    )
    if not environment or not policy.permits(environment.name, plan.workflow):
        raise ValueError("BATCH_ENVIRONMENT_NOT_AUTHORIZED")
    loaded, grouping = effective_input_groups(plan, loaded)
    summary = plan_summary(plan, loaded)
    if not summary["fits_budget"] or not 1 <= summary["requests"] <= 1000:
        raise ValueError("BATCH_PLAN_EXCEEDS_NATIVE_REQUEST_OR_MONEY_BOUND")
    cases = [row for row in loaded["items"] if row["split"] == "validation"]
    if plan.sample_limit:
        cases = cases[: plan.sample_limit]
    if not cases:
        raise ValueError("BATCH_VALIDATION_CASES_REQUIRED")
    for model in [plan.baseline, *plan.candidates]:
        input_file(
            [
                (str(uuid4()), model, case["case"]["input"])
                for _ in range(plan.repeats)
                for case in cases
            ]
        )
    return plan, cases, policy, environment, grouping


def authorize_batch(session, store, row, *, starting=False):
    plan_row = session.get(ExperimentPlanRecord, row.plan_id, populate_existing=True)
    if row.specification["price_reference"] != price_reference():
        raise ValueError("BATCH_PRICE_CONTRACT_CHANGED_REAPPROVAL_REQUIRED")
    if row.approval_hash != digest(row.specification):
        raise ValueError("BATCH_APPROVAL_INTEGRITY_FAILURE")
    if not plan_row or plan_row.specification["plan_hash"] != row.specification["plan_hash"]:
        raise ValueError("BATCH_PLAN_CHANGED_REAPPROVAL_REQUIRED")
    if not starting and (
        not row.deadline_at
        or row.deadline_at <= datetime.now(UTC)
        or row.state in {"CANCEL_REQUESTED", "CANCELLED", "FAILED", "COMPLETED"}
        or plan_row.approval_status != "consumed"
        or principal_role(session, row.project_id, row.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("BATCH_EXPIRED_CANCELLED_OR_APPROVER_REVOKED")
    return batch_inputs(session, store, row.project_id, plan_row, deadline=row.deadline_at)


def frozen_envelope(plan_row, environment, max_turnaround_seconds):
    return {
        "version": VERSION,
        "plan_id": str(plan_row.id),
        "plan_hash": plan_row.specification["plan_hash"],
        "execution_plan": plan_row.specification["public_specification"],
        "environment_id": str(environment.id),
        "price_reference": price_reference(),
        "provider_storage_approved": True,
        "zero_data_retention": False,
        "provider_file_expiry_seconds": 172800,
        "provider_completion_window": "24h",
        "max_turnaround_seconds": max_turnaround_seconds,
        "reservation": "full_online_native_upper_bound_per_item",
        "automatic_resubmission": False,
        "per_request_latency": None,
        "local_processing_budget": "cumulative active wall; provider wait and containment excluded",
        "heldout_quality_claim": False,
    }


def public_batch(row):
    return {
        "id": str(row.id),
        "plan_id": str(row.plan_id),
        "run_id": str(row.run_id) if row.run_id else None,
        "state": row.state,
        "approval_hash": row.approval_hash,
        "specification": row.specification,
        "report": row.report,
        "created_at": row.created_at.isoformat(),
        "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
        "provider_batches": [
            {
                key: part.get(key)
                for key in (
                    "model_key",
                    "state",
                    "batch_id",
                    "total",
                    "completed",
                    "failed",
                    "cleanup",
                )
            }
            for part in row.pieces
        ],
        "canonical_call_reservations": len(row.calls),
        "accounted_active_processing_us": row.active_elapsed_us - row.active_reserved_us,
        "uncertain_active_processing_us": row.active_uncertain_us,
        "held_active_processing_us": row.active_reserved_us,
    }
