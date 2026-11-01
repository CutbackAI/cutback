"""Current rights and immutable ownership for one approved routing program."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from cutback.evals.datasets import digest
from cutback.evals.routing_programs import RoutingProgram
from cutback.server.campaign_content import current_policy
from cutback.server.campaign_service import snapshot_cost
from cutback.server.db import DatasetRecord, Environment, ExperimentPlanRecord
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.plan_artifacts import load_plan, public_config
from cutback.server.principals import principal_role
from cutback.server.routing_program_db import RoutingProgramRecord, RoutingProgramRun

ACTIVE = {"QUEUED", "WAITING_STATIC", "WAITING_TRAINING", "WAITING_ROUTED", "WAITING_CONFIRMATION"}
PHASE_STATUS = {
    "static_validation": "WAITING_STATIC",
    "training": "WAITING_TRAINING",
    "routed_validation": "WAITING_ROUTED",
    "confirmation": "WAITING_CONFIRMATION",
}


def approval_hash(parent, payload_hash):
    return digest(
        {
            "contract": "cutback.routing-program-approval.v1",
            "program_id": str(parent.id),
            "project_id": str(parent.project_id),
            "environment_id": str(parent.environment_id),
            "dataset_id": str(parent.dataset_id),
            "policy_id": str(parent.policy_id),
            "template_plan_id": str(parent.template_plan_id),
            "template_plan_sha256": parent.template_plan_sha256,
            "snapshot_id": str(parent.snapshot_id),
            "snapshot_sha256": parent.snapshot_sha256,
            "payload_sha256": payload_hash,
            "economic_envelope": parent.economic_envelope,
            "expires_at": parent.expires_at.isoformat(),
        }
    )


def program_for_run(session, run, *, lock=True):
    """An orphaned marker never downgrades a child to standalone authorization."""
    planned = session.get(ExperimentPlanRecord, run.plan_id, populate_existing=True)
    marker = planned.specification.get("routing_program_id") if planned else None
    marked = planned is not None and any(
        key in planned.specification for key in ("routing_program_id", "routing_program_sha256")
    )
    link = session.scalar(select(RoutingProgramRun).where(RoutingProgramRun.run_id == run.id))
    if not marked and link is None:
        return None, None
    if not planned or not link:
        raise ValueError("ROUTING_PROGRAM_CHILD_OWNER_MISSING")
    parent = session.get(
        RoutingProgramRecord, link.program_id, with_for_update=lock, populate_existing=True
    )
    if (
        not parent
        or marker != str(link.program_id)
        or (link.project_id, parent.project_id, planned.project_id)
        != (run.project_id, run.project_id, run.project_id)
        or link.environment_id != parent.environment_id
        or link.program_sha256 != parent.approval_sha256
        or planned.specification.get("routing_program_sha256") != parent.approval_sha256
        or link.plan_sha256 != planned.specification.get("plan_hash")
        or approval_hash(parent, parent.request_sha256) != parent.approval_sha256
    ):
        raise ValueError("ROUTING_PROGRAM_CHILD_FROZEN_SCOPE_CHANGED")
    return parent, link


def load_program(session, store, parent):
    if parent.expires_at <= datetime.now(UTC):
        raise ValueError("ROUTING_PROGRAM_EXPIRED")
    policy = current_policy(
        session,
        parent.project_id,
        parent.policy_id,
        parent.specification["environment"],
        parent.specification["template"]["workflow"],
    )
    from cutback.server.routing_service import _payload

    sample, value = _payload(
        session, store, parent.project_id, parent.policy_id, parent.payload_id, policy
    )
    specification = RoutingProgram.model_validate(value["specification"])
    if (
        sample.metadata_safe.get("kind") != "routing_program_envelope"
        or sample.metadata_safe.get("routing_program_id") != str(parent.id)
        or sample.content_hash != parent.request_sha256
        or approval_hash(parent, sample.content_hash) != parent.approval_sha256
        or public_config(specification.model_dump(mode="json"), redact_content_hashes=True)
        != parent.specification
        or specification.dataset_id != parent.dataset_id
        or specification.baseline_snapshot_id != parent.snapshot_id
        or digest(specification.template.model_dump(mode="json")) != parent.template_plan_sha256
    ):
        raise ValueError("ROUTING_PROGRAM_ENVELOPE_CHANGED_REAPPROVAL_REQUIRED")
    dataset = session.get(DatasetRecord, parent.dataset_id, populate_existing=True)
    environment = session.get(Environment, parent.environment_id, populate_existing=True)
    template = session.get(ExperimentPlanRecord, parent.template_plan_id, populate_existing=True)
    if (
        not dataset
        or dataset.project_id != parent.project_id
        or dataset.immutable_hash != specification.template.dataset_hash
        or dataset.workflow_id != specification.template.workflow
        or dataset.provenance.get("environment") != specification.environment
        or dataset.provenance.get("policy_id") != str(parent.policy_id)
        or (dataset.provenance.get("independent_labels") is True)
        != parent.economic_envelope.get("independent_labels_declared")
        or not environment
        or environment.project_id != parent.project_id
        or environment.name != specification.environment
        or not template
        or template.project_id != parent.project_id
        or template.dataset_id != parent.dataset_id
        or template.specification.get("policy_id") != str(parent.policy_id)
        or template.specification.get("plan_hash") != parent.template_plan_sha256
        or load_plan(store, parent.project_id, template.specification) != specification.template
    ):
        raise ValueError("ROUTING_PROGRAM_TEMPLATE_DATASET_OR_ENVIRONMENT_CHANGED")
    if any(p not in policy.allowed_providers for p in specification.template.approved_providers):
        raise ValueError("ROUTING_PROGRAM_VENDOR_POLICY_DENIED")
    snapshot = session.get(TaskCostSnapshot, parent.snapshot_id, populate_existing=True)
    if not snapshot or snapshot.evidence_sha256 != parent.snapshot_sha256:
        raise ValueError("ROUTING_PROGRAM_BASELINE_ECONOMICS_CHANGED_OR_UNKNOWN")
    cost = snapshot_cost(
        snapshot, parent.project_id, parent.environment_id, specification.template.workflow
    )
    expected = parent.economic_envelope.get("baseline_complete_declared_cost_per_success_usd")
    if (str(cost) if cost is not None else None) != expected:
        raise ValueError("ROUTING_PROGRAM_BASELINE_ECONOMICS_CHANGED_OR_UNKNOWN")
    return specification, policy, dataset, value


def authorize_program(session, store, parent, *, synthetic_transport=False):
    now = datetime.now(UTC)
    if (
        parent.approval_status != "consumed"
        or parent.status not in ACTIVE
        or parent.deadline_at is None
        or parent.deadline_at <= now
        or principal_role(session, parent.project_id, parent.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("ROUTING_PROGRAM_CANCELLED_EXPIRED_OR_AUTHORITY_REVOKED")
    context = load_program(session, store, parent)
    if parent.started_at is None or parent.deadline_at != min(
        parent.expires_at,
        parent.started_at + timedelta(seconds=context[0].budget.max_wall_seconds),
    ):
        raise ValueError("ROUTING_PROGRAM_WHOLE_DEADLINE_CHANGED")
    if (
        context[0].template.routing_execution.transport == "scripted_test_only"
        and synthetic_transport is not True
    ):
        raise ValueError("SCRIPTED_ROUTING_PROGRAM_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR")
    return context


def authorize_program_run(session, store, run_id, *, synthetic_transport=False):
    from cutback.server.campaign_db import CampaignRun
    from cutback.server.db import ExperimentRunRecord

    run = session.get(ExperimentRunRecord, UUID(str(run_id)), populate_existing=True)
    if not run:
        raise ValueError("ROUTING_PROGRAM_RUN_UNAVAILABLE")
    parent, link = program_for_run(session, run)
    if parent is None:
        return
    if session.scalar(select(CampaignRun.id).where(CampaignRun.run_id == run.id)):
        raise ValueError("EXECUTION_MULTIPLE_PARENT_OWNERS_NOT_QUALIFIED")
    if parent.status != PHASE_STATUS[link.phase]:
        raise ValueError("ROUTING_PROGRAM_PHASE_NOT_ACTIVE")
    authorize_program(session, store, parent, synthetic_transport=synthetic_transport)


def source_proposal(session, store, parent):
    """Revalidate current selection, content, native gates and persistent test families."""
    from cutback.evals.experiments import ModelSpec, RouteSpec
    from cutback.routing_changes import RoutingSourceProposal
    from cutback.server.dataset_lineage import evaluation_receipt
    from cutback.server.db import ExperimentRunRecord
    from cutback.server.routing_program_db import RoutingProgramSelection
    from cutback.server.routing_service import validate_binding
    from cutback.server.stages import validate_confirmation

    specification, _, dataset, _ = load_program(session, store, parent)
    if (
        parent.status != "COMPLETED"
        or parent.requests_used != parent.known_cost_requests
        or principal_role(session, parent.project_id, parent.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("ROUTING_PROGRAM_COMPLETED_KNOWN_BILLING_AND_CURRENT_AUTHORITY_REQUIRED")
    link = session.scalar(
        select(RoutingProgramRun).where(
            RoutingProgramRun.program_id == parent.id, RoutingProgramRun.phase == "confirmation"
        )
    )
    selection = session.scalar(
        select(RoutingProgramSelection).where(RoutingProgramSelection.program_id == parent.id)
    )
    run = session.get(ExperimentRunRecord, link.run_id) if link else None
    if not run or not selection or run.status != "COMPLETED":
        raise ValueError("ROUTING_PROGRAM_FROZEN_CONFIRMATION_REQUIRED")
    program_for_run(session, run)
    row = session.get(ExperimentPlanRecord, run.plan_id)
    plan = load_plan(store, parent.project_id, row.specification)
    prior_run = session.get(ExperimentRunRecord, selection.validation_run_id)
    prior_row = session.get(ExperimentPlanRecord, prior_run.plan_id) if prior_run else None
    expected_selection = digest(
        {
            "contract": "cutback.routing-program-selection.v1",
            "program_hash": parent.approval_sha256,
            "validation_run_id": str(selection.validation_run_id),
            "validation_plan_hash": prior_row.specification["plan_hash"] if prior_row else None,
            "configuration_hash": selection.configuration_sha256,
            "strategy": selection.strategy,
        }
    )
    if (
        len(plan.candidates) != 1
        or selection.configuration_sha256 != digest(plan.candidates[0].model_dump(mode="json"))
        or selection.program_sha256 != parent.approval_sha256
        or selection.selection_sha256 != expected_selection
        or link.derivation.get("selection_sha256") != expected_selection
        or plan.predecessor_run_id != str(selection.validation_run_id)
    ):
        raise ValueError("ROUTING_PROGRAM_FROZEN_SELECTION_CHANGED")
    validate_confirmation(session, store, parent.project_id, plan, run_id=run.id)
    validate_binding(session, store, parent.project_id, plan, dataset_id=dataset.id)
    lineage = evaluation_receipt(session, parent.project_id, dataset.id, plan, run.id)
    decision: dict = next(
        (
            item
            for item in run.results.get("recommendations", [])
            if item.get("candidate")
            == public_config(plan.candidates[0].model_dump(mode="json"), redact_content_hashes=True)
        ),
        {},
    )
    if (
        run.results.get("billing_complete") is not True
        or decision.get("quality_gate_passed") is not True
        or decision.get("cheaper_on_sample") is not True
        or lineage.get("fresh_test_claim") is not True
    ):
        raise ValueError("ROUTING_PROGRAM_CURRENT_CONFIRMATION_GATES_NOT_PASSED")
    binding = specification.template.routing_execution.source
    proposed = plan.candidates[0]
    if selection.strategy == "static_native":
        proposed = ModelSpec(
            key=binding.configuration_key,
            provider="router",
            model=binding.configuration_model,
            routes=[RouteSpec(max_input_bytes=binding.max_input_bytes, target=proposed)],
        )
    return RoutingSourceProposal(
        run_id=run.id,
        plan_hash=row.specification["plan_hash"],
        dataset_hash=plan.dataset_hash,
        binding=binding,
        baseline=plan.baseline,
        proposed=proposed,
        decision="synthetic_local_review_only"
        if dataset.provenance["source_type"] == "synthetic_fixture"
        else "heldout_review_required"
        if decision.get("status") == "VERIFIED_ON_HELDOUT_DATASET"
        else "sample_review_required",
    )
