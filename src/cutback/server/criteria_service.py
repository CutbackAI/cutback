"""Bind reviewed criteria to exact plans and the canonical pre-call authority."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from cutback.core.evaluation_criteria import ReviewedCriteriaBinding
from cutback.core.semantic_rubrics import SemanticRubricRequirements
from cutback.evals.criteria_generation import (
    CriteriaGenerationPlan,
    decode,
    generation_contract,
    generation_input,
    materialize,
    parse_requirements,
)
from cutback.evals.datasets import digest
from cutback.server.criteria_content import current_policy, read_payload, training_view
from cutback.server.criteria_db import CriteriaExperimentUse, CriteriaVersionRecord
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.principals import principal_role


def load_generation(store, project, metadata):
    if datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(UTC) or metadata.get(
        "artifact_deleted"
    ):
        raise ValueError("CRITERIA_GENERATION_PLAN_EXPIRED")
    try:
        raw = json.loads(store.get(project, metadata["artifact_ref"]))
        plan = CriteriaGenerationPlan.model_validate(raw)
    except Exception:
        raise ValueError("CRITERIA_GENERATION_PLAN_UNAVAILABLE_OR_INVALID") from None
    if (
        digest(raw) != metadata["plan_hash"]
        or digest(plan.model_dump(mode="json")) != metadata["plan_hash"]
    ):
        raise ValueError("CRITERIA_GENERATION_FROZEN_PLAN_CHANGED")
    return plan


def read_input(session, store, row, policy):
    value, sha = read_payload(
        session, store, row, row.input_payload_id, policy, kind="criteria_input"
    )
    if not isinstance(value, dict) or set(value) != {
        "input",
        "requirements",
        "training",
        "source_item_ids",
    }:
        raise ValueError("CRITERIA_FROZEN_TRAINING_INPUT_CHANGED")
    requirements = parse_requirements(value["requirements"])
    if generation_input(requirements, value["training"]) != value["input"]:
        raise ValueError("CRITERIA_FROZEN_GENERATOR_FRAME_CHANGED")
    return value, sha, requirements


def prepared_generation(session, store, row):
    dataset, policy = current_policy(session, row)
    plan_row = session.get(ExperimentPlanRecord, row.generation_plan_id, populate_existing=True)
    if (
        not plan_row
        or plan_row.project_id != row.project_id
        or plan_row.dataset_id != row.dataset_id
        or plan_row.specification.get("plan_hash") != row.generation_plan_sha256
    ):
        raise ValueError("CRITERIA_GENERATION_APPROVAL_OR_SOURCE_PLAN_CHANGED")
    plan = load_generation(store, row.project_id, plan_row.specification)
    if (
        plan.criteria_id != row.id
        or plan.dataset_hash != row.source_dataset_sha256
        or plan.workflow != dataset.workflow_id
        or plan.input_payload_id != row.input_payload_id
    ):
        raise ValueError("CRITERIA_GENERATION_SCOPE_OR_TRANSPORT_CHANGED")
    value, sha, requirements = read_input(session, store, row, policy)
    if sha != plan.input_sha256 or plan.operation != generation_contract(requirements)["operation"]:
        raise ValueError("CRITERIA_GENERATION_APPROVED_INPUT_CHANGED")
    rows, identities = training_view(
        session, store, row.project_id, dataset, policy, limit=len(value["training"])
    )
    if rows != value["training"] or identities != value["source_item_ids"]:
        raise ValueError("CRITERIA_TRAINING_SOURCE_CHANGED_AFTER_APPROVAL")
    return plan, policy, value, requirements, plan_row


def generation_authority(session, store, row, *, synthetic_transport=False):
    plan, policy, value, requirements, plan_row = prepared_generation(session, store, row)
    if (
        row.state not in {"QUEUED", "RUNNING"}
        or principal_role(session, row.project_id, row.generation_approved_by or "")
        not in {"admin", "project_admin"}
        or plan_row.approval_status != "consumed"
        or plan_row.approved_by != row.generation_approved_by
        or plan.transport == "scripted_test_only"
        and synthetic_transport is not True
    ):
        raise ValueError("CRITERIA_GENERATION_APPROVAL_OR_TRANSPORT_NO_LONGER_AUTHORIZED")
    return plan, policy, value, requirements


def reserve_criteria_generation(session, run, environment, plan_sha256, amount):
    """A distinct approved parent; ordinary campaign-only proposer authority stays intact."""
    row = session.scalar(
        select(CriteriaVersionRecord)
        .where(
            CriteriaVersionRecord.project_id == run.project_id,
            CriteriaVersionRecord.generation_run_id == run.id,
        )
        .with_for_update()
    )
    if (
        not row
        or row.environment_id != environment
        or row.generation_plan_id != run.plan_id
        or row.generation_plan_sha256 != plan_sha256
        or row.state != "RUNNING"
    ):
        raise ValueError("CRITERIA_GENERATOR_REQUIRES_ITS_EXACT_APPROVED_PARENT")
    current_policy(session, row)
    if principal_role(session, run.project_id, row.generation_approved_by or "") not in {
        "admin",
        "project_admin",
    }:
        raise ValueError("CRITERIA_GENERATION_APPROVING_PRINCIPAL_REVOKED")
    if run.requests_used != 0:
        raise ValueError("CRITERIA_ONE_CALL_ALREADY_RESERVED_NO_REPLAY")


def version_sha256(row, input_sha, output_sha):
    return digest(
        {
            "criteria_id": str(row.id),
            "generation_plan_sha256": row.generation_plan_sha256,
            "input_sha256": input_sha,
            "output_sha256": output_sha,
        }
    )


def reviewed_content(session, store, row):
    dataset, policy = current_policy(session, row)
    if row.state != "COMPLETED" or row.output_payload_id is None or row.generation_run_id is None:
        raise ValueError("CRITERIA_COMPLETE_VALID_GENERATION_REQUIRED")
    run = session.get(ExperimentRunRecord, row.generation_run_id, populate_existing=True)
    if (
        not run
        or run.project_id != row.project_id
        or run.plan_id != row.generation_plan_id
        or run.status != "COMPLETED"
        or run.requests_used != 1
        or run.known_cost_requests != 1
        or run.results.get("criteria_version_sha256") != row.version_sha256
    ):
        raise ValueError("CRITERIA_GENERATION_ACCOUNTING_OR_VERSION_CHANGED")
    value, input_sha, requirements = read_input(session, store, row, policy)
    output, output_sha = read_payload(
        session, store, row, row.output_payload_id, policy, kind="criteria_output"
    )
    if (
        not isinstance(output, dict)
        or set(output) != {"output"}
        or version_sha256(row, input_sha, output_sha) != row.version_sha256
    ):
        raise ValueError("CRITERIA_REVIEW_CONTENT_VERSION_CHANGED")
    proposal = decode(
        output["output"], requirements, {entry["anchor"] for entry in value["training"]}
    )
    return requirements, proposal, dataset


def validate_binding(session, store, project, plan):
    if plan.criteria_binding is None:
        return None
    row = session.get(
        CriteriaVersionRecord, plan.criteria_binding.criteria_id, populate_existing=True
    )
    if (
        not row
        or row.project_id != project
        or row.version_sha256 != plan.criteria_binding.version_sha256
        or row.review_status != "APPROVED"
        or row.review_approval_id != plan.criteria_binding.review_approval_id
        or principal_role(session, project, row.reviewed_by or "") not in {"admin", "project_admin"}
    ):
        raise ValueError("CRITERIA_REVIEWED_VERSION_UNAVAILABLE_OR_NOT_APPROVED")
    requirements, proposal, dataset = reviewed_content(session, store, row)
    if isinstance(requirements, SemanticRubricRequirements):
        from cutback.evals.metrics import MetricSpec
        from cutback.evals.rubric_generation import materialize_rubrics, rubric_judge

        expected_metric = MetricSpec(
            kind="episode_contract",
            minimum=requirements.minimum,
            max_regression=requirements.max_regression,
            min_test_samples=requirements.min_test_samples,
            min_test_groups=requirements.min_test_groups,
        )
        if (
            plan.workflow != dataset.workflow_id
            or plan.evaluator_version != "deterministic-v14"
            or not plan.episode_execution
            or plan.metric != expected_metric
            or plan.episode_execution.semantic_judging
            != materialize_rubrics(proposal, requirements)
            or plan.judge != rubric_judge(requirements)
        ):
            raise ValueError("EXPERIMENT_MUST_PIN_EXACT_REVIEWED_SEMANTIC_RUBRICS_AND_JUDGE")
        return row
    criteria = materialize(proposal, requirements)
    if (
        plan.workflow != dataset.workflow_id
        or plan.criteria != criteria
        or plan.metric != criteria[0].metric
    ):
        raise ValueError("EXPERIMENT_MUST_PIN_EXACT_REVIEWED_PRIMARY_AND_REQUIRED_CRITERIA")
    return row


def register_binding(session, project, plan_id, plan, row):
    if row is not None:
        session.add(
            CriteriaExperimentUse(
                project_id=project,
                plan_id=plan_id,
                criteria_id=row.id,
                version_sha256=row.version_sha256,
                review_approval_id=row.review_approval_id,
            )
        )


def receipt(session, project, plan):
    if not plan.criteria_binding:
        return {}
    binding = ReviewedCriteriaBinding.model_validate(plan.criteria_binding)
    row = session.get(CriteriaVersionRecord, binding.criteria_id)
    run = (
        session.get(ExperimentRunRecord, row.generation_run_id)
        if row and row.project_id == project
        else None
    )
    complete = bool(
        run and run.status == "COMPLETED" and run.requests_used == run.known_cost_requests == 1
    )
    return {
        "criteria_binding": binding.model_dump(mode="json"),
        "criteria_review_assurance": "owner_review_not_independent_judge_calibration",
        "criteria_confirmation_predecessor_run_id": plan.predecessor_run_id,
        "criteria_generation_cost": {
            "generation_run_id": str(run.id) if run else None,
            "known_spend_subtotal_usd": str(run.spent_usd) if run else None,
            "billing_complete": complete,
            "shared_one_time_setup": True,
            "included_in_target_evaluation_spend": False,
            "amortized_per_task_usd": None,
            "full_task_economics_known": False,
        },
    }
