"""Current generation and review authority; a proposal never grants execution rights."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from cutback.core.scenario_proposals import ScenarioRequirements
from cutback.evals.datasets import Case, digest
from cutback.evals.scenario_generation import (
    ScenarioGenerationPlan,
    decode,
    generation_input,
    materialize,
)
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.principals import principal_role
from cutback.server.scenario_content import current_policy, read_payload, training_view
from cutback.server.scenario_db import ScenarioVersionRecord


def load_generation(store, project, metadata):
    if datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(UTC) or metadata.get(
        "artifact_deleted"
    ):
        raise ValueError("SCENARIO_GENERATION_PLAN_EXPIRED")
    try:
        blob = store.get(project, metadata["artifact_ref"])
        if len(blob) > 256000:
            raise ValueError
        raw = json.loads(blob)
        plan = ScenarioGenerationPlan.model_validate(raw)
        if (
            digest(raw) != metadata["plan_hash"]
            or digest(plan.model_dump(mode="json")) != metadata["plan_hash"]
        ):
            raise ValueError
    except Exception:
        raise ValueError("SCENARIO_GENERATION_FROZEN_PLAN_UNAVAILABLE_OR_CHANGED") from None
    return plan


def read_input(session, store, row, policy):
    value, sha = read_payload(
        session, store, row, row.input_payload_id, policy, kind="scenario_input"
    )
    try:
        if not isinstance(value, dict) or set(value) != {
            "input",
            "requirements",
            "training",
            "source_item_ids",
        }:
            raise ValueError
        requirements = ScenarioRequirements.model_validate(value["requirements"])
        sources = {key: Case.model_validate(case) for key, case in value["training"].items()}
        if generation_input(requirements, sources) != value["input"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("SCENARIO_FROZEN_GENERATOR_INPUT_CHANGED") from None
    return value, sha, requirements


def prepared_generation(session, store, row):
    dataset, policy = current_policy(session, row)
    planned = session.get(ExperimentPlanRecord, row.generation_plan_id, populate_existing=True)
    if (
        not planned
        or planned.project_id != row.project_id
        or planned.dataset_id != row.dataset_id
        or planned.specification.get("plan_hash") != row.generation_plan_sha256
    ):
        raise ValueError("SCENARIO_GENERATION_APPROVAL_OR_SOURCE_PLAN_CHANGED")
    plan = load_generation(store, row.project_id, planned.specification)
    if (
        plan.scenario_id != row.id
        or plan.dataset_hash != row.source_dataset_sha256
        or plan.workflow != dataset.workflow_id
        or plan.input_payload_id != row.input_payload_id
    ):
        raise ValueError("SCENARIO_GENERATION_SCOPE_CHANGED")
    value, sha, requirements = read_input(session, store, row, policy)
    if sha != plan.input_sha256:
        raise ValueError("SCENARIO_GENERATION_APPROVED_INPUT_CHANGED")
    sources, identities = training_view(
        session, store, row.project_id, dataset, policy, limit=len(value["training"])
    )
    if {key: case.model_dump(mode="json") for key, case in sources.items()} != value[
        "training"
    ] or identities != value["source_item_ids"]:
        raise ValueError("SCENARIO_TRAINING_SOURCE_CHANGED_AFTER_APPROVAL")
    return plan, policy, value, requirements, planned


def generation_authority(session, store, row, *, synthetic_transport=False):
    plan, policy, value, requirements, planned = prepared_generation(session, store, row)
    if (
        row.state not in {"QUEUED", "RUNNING"}
        or principal_role(session, row.project_id, row.generation_approved_by or "")
        not in {"admin", "project_admin"}
        or planned.approval_status != "consumed"
        or planned.approved_by != row.generation_approved_by
        or (plan.transport == "scripted_test_only" and synthetic_transport is not True)
    ):
        raise ValueError("SCENARIO_GENERATION_APPROVAL_OR_TRANSPORT_NO_LONGER_AUTHORIZED")
    return plan, policy, value, requirements


def reserve_scenario_generation(session, run, environment, plan_sha256, amount):
    row = session.scalar(
        select(ScenarioVersionRecord)
        .where(
            ScenarioVersionRecord.project_id == run.project_id,
            ScenarioVersionRecord.generation_run_id == run.id,
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
        raise ValueError("SCENARIO_GENERATOR_REQUIRES_ITS_EXACT_APPROVED_PARENT")
    current_policy(session, row)
    planned = session.get(ExperimentPlanRecord, row.generation_plan_id, populate_existing=True)
    if (
        not planned
        or planned.approval_status != "consumed"
        or planned.approved_by != row.generation_approved_by
        or principal_role(session, run.project_id, row.generation_approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("SCENARIO_GENERATION_APPROVING_PRINCIPAL_REVOKED")
    if run.requests_used != 0:
        raise ValueError("SCENARIO_ONE_CALL_ALREADY_RESERVED_NO_REPLAY")


def version_sha256(row, input_sha, output_sha):
    return digest(
        {
            "scenario_id": str(row.id),
            "generation_plan_sha256": row.generation_plan_sha256,
            "input_sha256": input_sha,
            "output_sha256": output_sha,
        }
    )


def reviewed_content(session, store, row):
    _, _, value, requirements, _ = prepared_generation(session, store, row)
    _, policy = current_policy(session, row)
    if row.state != "COMPLETED" or row.output_payload_id is None or row.generation_run_id is None:
        raise ValueError("SCENARIO_COMPLETE_VALID_GENERATION_REQUIRED")
    run = session.get(ExperimentRunRecord, row.generation_run_id, populate_existing=True)
    if (
        not run
        or run.project_id != row.project_id
        or run.plan_id != row.generation_plan_id
        or run.status != "COMPLETED"
        or run.requests_used != 1
        or run.known_cost_requests != 1
        or run.results.get("scenario_version_sha256") != row.version_sha256
    ):
        raise ValueError("SCENARIO_GENERATION_ACCOUNTING_OR_VERSION_CHANGED")
    _, input_sha, _ = read_input(session, store, row, policy)
    output, output_sha = read_payload(
        session, store, row, row.output_payload_id, policy, kind="scenario_output"
    )
    if (
        not isinstance(output, dict)
        or set(output) != {"output"}
        or version_sha256(row, input_sha, output_sha) != row.version_sha256
    ):
        raise ValueError("SCENARIO_REVIEW_CONTENT_VERSION_CHANGED")
    sources = {key: Case.model_validate(case) for key, case in value["training"].items()}
    proposal = decode(output["output"], requirements, sources)
    return requirements, proposal, materialize(proposal, requirements, sources)


def require_current_import(session, store, dataset):
    """A generated development dataset cannot outlive its exact current review."""
    from cutback.server.scenario_db import ScenarioDatasetImport

    link = session.get(ScenarioDatasetImport, (dataset.project_id, dataset.id))
    declared = dataset.provenance.get("generated_scenario")
    if link is None:
        if declared is not None:
            raise ValueError("SCENARIO_IMPORT_AUTHORITY_MISSING")
        return
    row = session.get(ScenarioVersionRecord, link.scenario_id, populate_existing=True)
    if (
        not row
        or row.project_id != dataset.project_id
        or row.review_status != "APPROVED"
        or row.review_approval_id != link.review_approval_id
        or row.version_sha256 != link.version_sha256
        or principal_role(session, dataset.project_id, row.reviewed_by or "")
        not in {"admin", "project_admin"}
        or declared
        != {
            "scenario_id": str(row.id),
            "version_sha256": link.version_sha256,
            "review_approval_id": str(link.review_approval_id),
            "development_only": True,
        }
        or dataset.provenance.get("independent_labels") is not False
        or dataset.provenance.get("representative") is not False
    ):
        raise ValueError("SCENARIO_IMPORTED_VERSION_OR_REVIEW_NO_LONGER_AUTHORIZED")
    reviewed_content(session, store, row)
