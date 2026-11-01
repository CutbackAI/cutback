"""Current rights and immutable ownership for one approved common-policy program."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from cutback.evals.datasets import digest
from cutback.evals.source_policy_programs import SourcePolicyProgram
from cutback.server.campaign_content import current_policy
from cutback.server.campaign_service import snapshot_cost
from cutback.server.db import DatasetRecord, Environment, ExperimentPlanRecord, PayloadSample
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.plan_artifacts import load_plan, public_config
from cutback.server.policy_program_db import (
    SourcePolicyProgramPayload,
    SourcePolicyProgramRecord,
    SourcePolicyProgramRun,
)
from cutback.server.principals import principal_role

ACTIVE = {
    "QUEUED",
    "WAITING_STATIC",
    "WAITING_TRAINING",
    "WAITING_ROUTED",
    "WAITING_CONFIRMATION",
    "WAITING_GENERATION",
    "WAITING_GENERATED",
}
PHASE_STATUS = {
    "static_validation": "WAITING_STATIC",
    "generation": "WAITING_GENERATION",
    "generated_validation": "WAITING_GENERATED",
    "training": "WAITING_TRAINING",
    "routed_validation": "WAITING_ROUTED",
    "confirmation": "WAITING_CONFIRMATION",
}


def approval_hash(parent, payload_hash):
    return digest(
        {
            "contract": "cutback.source-policy-program-approval.v1",
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
    marker = planned.specification.get("source_policy_program_id") if planned else None
    marked = planned is not None and any(
        key in planned.specification
        for key in ("source_policy_program_id", "source_policy_program_sha256")
    )
    link = session.scalar(
        select(SourcePolicyProgramRun).where(SourcePolicyProgramRun.run_id == run.id)
    )
    if not marked and link is None:
        return None, None
    if not planned or not link:
        raise ValueError("POLICY_PROGRAM_CHILD_OWNER_MISSING")
    from cutback.server.campaign_db import CampaignRun
    from cutback.server.routing_program_db import RoutingProgramRun

    if session.scalar(select(CampaignRun.id).where(CampaignRun.run_id == run.id)) or session.scalar(
        select(RoutingProgramRun.id).where(RoutingProgramRun.run_id == run.id)
    ):
        raise ValueError("EXECUTION_MULTIPLE_PARENT_OWNERS_NOT_QUALIFIED")
    parent = session.get(
        SourcePolicyProgramRecord, link.program_id, with_for_update=lock, populate_existing=True
    )
    if (
        not parent
        or marker != str(link.program_id)
        or (link.project_id, parent.project_id, planned.project_id)
        != (run.project_id, run.project_id, run.project_id)
        or link.environment_id != parent.environment_id
        or link.program_sha256 != parent.approval_sha256
        or planned.specification.get("source_policy_program_sha256") != parent.approval_sha256
        or link.plan_sha256 != planned.specification.get("plan_hash")
        or approval_hash(parent, parent.request_sha256) != parent.approval_sha256
    ):
        raise ValueError("POLICY_PROGRAM_CHILD_FROZEN_SCOPE_CHANGED")
    return parent, link


def load_program(session, store, parent):
    if parent.expires_at <= datetime.now(UTC):
        raise ValueError("POLICY_PROGRAM_EXPIRED")
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
    specification = SourcePolicyProgram.model_validate(value["specification"])
    from cutback.pricing import Catalog

    if specification.template.price_sha256 != Catalog().sha256:
        raise ValueError("POLICY_PROGRAM_PRICE_CHANGED_REAPPROVAL_REQUIRED")
    if (
        sample.metadata_safe.get("kind") != "source_policy_program_envelope"
        or sample.metadata_safe.get("source_policy_program_id") != str(parent.id)
        or sample.content_hash != parent.request_sha256
        or approval_hash(parent, sample.content_hash) != parent.approval_sha256
        or public_config(specification.model_dump(mode="json"), redact_content_hashes=True)
        != parent.specification
        or specification.dataset_id != parent.dataset_id
        or specification.baseline_snapshot_id != parent.snapshot_id
        or digest(specification.template.model_dump(mode="json")) != parent.template_plan_sha256
    ):
        raise ValueError("POLICY_PROGRAM_ENVELOPE_CHANGED_REAPPROVAL_REQUIRED")
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
        raise ValueError("POLICY_PROGRAM_TEMPLATE_DATASET_OR_ENVIRONMENT_CHANGED")
    if any(p not in policy.allowed_providers for p in specification.template.approved_providers):
        raise ValueError("POLICY_PROGRAM_VENDOR_POLICY_DENIED")
    snapshot = session.get(TaskCostSnapshot, parent.snapshot_id, populate_existing=True)
    if not snapshot or snapshot.evidence_sha256 != parent.snapshot_sha256:
        raise ValueError("POLICY_PROGRAM_BASELINE_ECONOMICS_CHANGED_OR_UNKNOWN")
    cost = snapshot_cost(
        snapshot, parent.project_id, parent.environment_id, specification.template.workflow
    )
    expected = parent.economic_envelope.get("baseline_complete_declared_cost_per_success_usd")
    if (str(cost) if cost is not None else None) != expected:
        raise ValueError("POLICY_PROGRAM_BASELINE_ECONOMICS_CHANGED_OR_UNKNOWN")
    from cutback.server.policy_service import validate_binding

    validate_binding(
        session, store, parent.project_id, specification.template, dataset.id, read=False
    )
    execution = specification.template.source_policy_execution
    if execution is None:
        raise ValueError("POLICY_PROGRAM_SOURCE_EXECUTION_REQUIRED")
    for reference in [execution.reference, *execution.candidates]:
        require_link(session, parent, reference.payload_id, reference.payload_sha256)
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
        raise ValueError("POLICY_PROGRAM_CANCELLED_EXPIRED_OR_AUTHORITY_REVOKED")
    context = load_program(session, store, parent)
    if parent.started_at is None or parent.deadline_at != min(
        parent.expires_at,
        parent.started_at + timedelta(seconds=context[0].budget.max_wall_seconds),
    ):
        raise ValueError("POLICY_PROGRAM_WHOLE_DEADLINE_CHANGED")
    if (
        context[0].template.source_policy_execution.transport == "scripted_test_only"
        and synthetic_transport is not True
    ):
        raise ValueError("SCRIPTED_POLICY_PROGRAM_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR")
    return context


def authorize_program_run(session, store, run_id, *, synthetic_transport=False):
    from cutback.server.campaign_db import CampaignRun
    from cutback.server.db import ExperimentRunRecord

    run = session.get(ExperimentRunRecord, UUID(str(run_id)), populate_existing=True)
    if not run:
        raise ValueError("POLICY_PROGRAM_RUN_UNAVAILABLE")
    parent, link = program_for_run(session, run)
    if parent is None:
        return
    if session.scalar(select(CampaignRun.id).where(CampaignRun.run_id == run.id)):
        raise ValueError("EXECUTION_MULTIPLE_PARENT_OWNERS_NOT_QUALIFIED")
    from cutback.server.routing_program_service import program_for_run as routing_owner

    if routing_owner(session, run, lock=False)[0] is not None:
        raise ValueError("EXECUTION_MULTIPLE_PARENT_OWNERS_NOT_QUALIFIED")
    if parent.status != PHASE_STATUS[link.phase]:
        raise ValueError("POLICY_PROGRAM_PHASE_NOT_ACTIVE")
    specification, _, _, _ = authorize_program(
        session, store, parent, synthetic_transport=synthetic_transport
    )
    if (
        link.phase in {"generation", "generated_validation"}
        and link.round_index != parent.current_round
    ):
        raise ValueError("POLICY_PROGRAM_FROZEN_ROUND_NOT_ACTIVE")
    planned = session.get(ExperimentPlanRecord, run.plan_id, populate_existing=True)
    if link.phase == "generation":
        from cutback.server.policy_generation_worker import load_generation

        child = load_generation(store, parent.project_id, planned.specification)
        if (
            child.program_id != parent.id
            or child.program_sha256 != parent.approval_sha256
            or child.round_index != link.round_index
            or specification.generator is None
            or child.workflow != specification.template.workflow
            or child.dataset_hash != specification.template.dataset_hash
        ):
            raise ValueError("POLICY_PROGRAM_GENERATOR_SCOPE_CHANGED")
        from cutback.evals.campaigns import proposal_model

        if child.baseline != proposal_model(specification.generator):
            raise ValueError("POLICY_PROGRAM_GENERATOR_MODEL_CHANGED")
        require_link(
            session, parent, child.input_payload_id, child.input_sha256, kinds={"generation_input"}
        )
    else:
        child = load_plan(store, parent.project_id, planned.specification)
        execution, original = (
            child.source_policy_execution,
            specification.template.source_policy_execution,
        )
        if (
            execution is None
            or execution.source != original.source
            or execution.reference != original.reference
            or execution.transport != original.transport
            or child.dataset_hash != specification.template.dataset_hash
            or child.metric != specification.template.metric
            or child.criteria != specification.template.criteria
            or child.content_nonce != specification.template.content_nonce
            or execution.phase != ("training" if link.phase == "training" else "evaluation")
            or child.split
            != (
                "train"
                if link.phase == "training"
                else "test"
                if link.phase == "confirmation"
                else "validation"
            )
        ):
            raise ValueError("POLICY_PROGRAM_CHILD_COMPARISON_FRAME_CHANGED")
        for reference in [execution.reference, *execution.candidates]:
            owned = require_link(
                session,
                parent,
                reference.payload_id,
                reference.payload_sha256,
                kinds={"reference", "static", "generated", "learned"},
            )
            if owned.derivation.get("reference") != reference.model_dump(mode="json"):
                raise ValueError("POLICY_PROGRAM_CHILD_POLICY_REFERENCE_CHANGED")


def require_link(session, parent, payload_id, content_sha256, *, kinds=None):
    link = session.scalar(
        select(SourcePolicyProgramPayload).where(
            SourcePolicyProgramPayload.program_id == parent.id,
            SourcePolicyProgramPayload.payload_id == payload_id,
        )
    )
    sample = session.get(PayloadSample, payload_id, populate_existing=True)
    if (
        not link
        or not sample
        or link.project_id != parent.project_id
        or link.environment_id != parent.environment_id
        or sample.project_id != parent.project_id
        or sample.policy_id != parent.policy_id
        or link.program_sha256 != parent.approval_sha256
        or link.content_sha256 != content_sha256
        or sample.content_hash != content_sha256
        or sample.expires_at <= datetime.now(UTC)
        or sample.metadata_safe.get("deleted")
        or kinds is not None
        and link.kind not in kinds
    ):
        raise ValueError("POLICY_PROGRAM_CURRENT_OWNED_CONTENT_REQUIRED")
    return link


def link_payload(session, parent, sample, kind, *, derivation=None):
    if sample.project_id != parent.project_id or sample.policy_id != parent.policy_id:
        raise ValueError("POLICY_PROGRAM_CONTENT_SCOPE_CHANGED")
    if session.scalar(
        select(SourcePolicyProgramPayload.id).where(
            SourcePolicyProgramPayload.program_id == parent.id,
            SourcePolicyProgramPayload.payload_id == sample.id,
        )
    ):
        raise ValueError("POLICY_PROGRAM_PAYLOAD_ALREADY_FROZEN")
    ordinal = session.scalar(
        select(func.max(SourcePolicyProgramPayload.ordinal)).where(
            SourcePolicyProgramPayload.program_id == parent.id,
        )
    )
    ordinal = ordinal + 1 if ordinal is not None else 0
    if ordinal > 64:
        raise ValueError("POLICY_PROGRAM_CONTENT_DERIVATION_BOUND")
    row = SourcePolicyProgramPayload(
        program_id=parent.id,
        project_id=parent.project_id,
        environment_id=parent.environment_id,
        payload_id=sample.id,
        content_sha256=sample.content_hash,
        kind=kind,
        ordinal=ordinal,
        program_sha256=parent.approval_sha256,
        derivation=derivation or {},
    )
    session.add(row)
    session.flush()
    return row


def source_proposal(session, store, parent):
    """Current one-choice evidence, private derivation and exact original source frame."""
    from cutback.evals.policy_artifacts import decode_policy_bundle
    from cutback.evals.source_policy_programs import policy_confirmation_gate
    from cutback.policy_changes import SourcePolicyProposal
    from cutback.server.dataset_lineage import evaluation_receipt
    from cutback.server.db import ExperimentRunRecord
    from cutback.server.policy_program_db import SourcePolicyProgramSelection
    from cutback.server.policy_service import validate_binding
    from cutback.server.stages import validate_confirmation

    specification, _, dataset, _ = load_program(session, store, parent)
    if (
        parent.status != "COMPLETED"
        or parent.requests_used != parent.known_cost_requests
        or principal_role(session, parent.project_id, parent.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("POLICY_PROGRAM_COMPLETED_CURRENT_AUTHORITY_AND_KNOWN_ACCOUNTING_REQUIRED")
    link = session.scalar(
        select(SourcePolicyProgramRun).where(
            SourcePolicyProgramRun.program_id == parent.id,
            SourcePolicyProgramRun.phase == "confirmation",
        )
    )
    selection = session.scalar(
        select(SourcePolicyProgramSelection).where(
            SourcePolicyProgramSelection.program_id == parent.id,
        )
    )
    run = session.get(ExperimentRunRecord, link.run_id) if link else None
    if (
        not run
        or not selection
        or run.status != "COMPLETED"
        or run.call_ledger_version != 1
        or run.requests_used != run.known_cost_requests
    ):
        raise ValueError("POLICY_PROGRAM_CURRENT_CANONICAL_CONFIRMATION_REQUIRED")
    program_for_run(session, run)
    row = session.get(ExperimentPlanRecord, run.plan_id)
    plan = load_plan(store, parent.project_id, row.specification)
    execution, original = (
        plan.source_policy_execution,
        specification.template.source_policy_execution,
    )
    if (
        execution is None
        or original is None
        or len(execution.candidates) != 1
        or execution.source != original.source
        or execution.reference != original.reference
        or execution.transport != original.transport
        or plan.workflow != specification.template.workflow
        or plan.dataset_hash != specification.template.dataset_hash
        or plan.metric != specification.template.metric
        or plan.criteria != specification.template.criteria
        or plan.content_nonce != specification.template.content_nonce
    ):
        raise ValueError("POLICY_PROGRAM_CONFIRMED_COMPARISON_FRAME_CHANGED")
    selected = execution.candidates[0]
    prior_run = session.get(ExperimentRunRecord, selection.validation_run_id)
    prior_link = session.scalar(
        select(SourcePolicyProgramRun).where(
            SourcePolicyProgramRun.program_id == parent.id,
            SourcePolicyProgramRun.run_id == selection.validation_run_id,
        )
    )
    prior_row = session.get(ExperimentPlanRecord, prior_run.plan_id) if prior_run else None
    expected_selection = digest(
        {
            "contract": "cutback.source-policy-selection.v1",
            "program_hash": parent.approval_sha256,
            "validation_run_id": str(selection.validation_run_id),
            "validation_plan_hash": prior_row.specification["plan_hash"] if prior_row else None,
            "selected_reference": selected.model_dump(mode="json"),
            "strategy": selection.strategy,
        }
    )
    if (
        not prior_link
        or prior_link.phase
        not in {"static_validation", "generated_validation", "routed_validation"}
        or selection.program_sha256 != parent.approval_sha256
        or selection.candidate_key != selected.key
        or selection.configuration_sha256 != selected.policy_sha256
        or selection.selection_sha256 != expected_selection
        or link.derivation.get("selection_sha256") != expected_selection
        or plan.predecessor_run_id != str(selection.validation_run_id)
    ):
        raise ValueError("POLICY_PROGRAM_FROZEN_SELECTION_CHANGED")
    validate_confirmation(session, store, parent.project_id, plan, run_id=run.id)
    payloads = validate_binding(session, store, parent.project_id, plan, dataset.id)
    policies = decode_policy_bundle(payloads, execution)
    lineage = evaluation_receipt(session, parent.project_id, dataset.id, plan, run.id)
    report = {**run.results, "lineage": lineage}
    if (
        lineage.get("fresh_test_claim") is not True
        or not policy_confirmation_gate(report, plan, sample_only=True)["passed"]
    ):
        raise ValueError("POLICY_PROGRAM_CURRENT_SOURCE_CONFIRMATION_GATES_NOT_PASSED")
    owned = require_link(
        session,
        parent,
        selected.payload_id,
        selected.payload_sha256,
        kinds={"static", "generated", "learned"},
    )
    if owned.derivation.get("reference") != selected.model_dump(mode="json"):
        raise ValueError("POLICY_PROGRAM_SELECTED_CONTENT_DERIVATION_CHANGED")
    proposed = policies[selected.key]
    if owned.kind == "generated":
        from cutback.evals.policy_generation import decode_policy_proposals
        from cutback.server.policy_program_content import read_content
        from cutback.server.policy_program_worker import (
            load_policies,
            selected_static,
        )

        generation = session.get(ExperimentRunRecord, UUID(owned.derivation["generation_run_id"]))
        owner, generated_link = program_for_run(session, generation) if generation else (None, None)
        if (
            owner is None
            or generated_link is None
            or owner.id != parent.id
            or generated_link.phase != "generation"
            or generation.status != "COMPLETED"
            or generation.requests_used != 1
            or generation.known_cost_requests != 1
            or generation.results.get("billing_complete") is not True
        ):
            raise ValueError("POLICY_PROGRAM_CURRENT_GENERATION_EVIDENCE_REQUIRED")
        _, current_policy, _, _ = load_program(session, store, parent)
        output = read_content(
            session,
            store,
            parent,
            UUID(generation.results["output_payload_id"]),
            generation.results["output_sha256"],
            current_policy,
            kind="generation_output",
        )
        _, static = load_policies(
            session,
            store,
            parent,
            specification,
            selected_static(specification, parent.economic_envelope),
        )
        old_refs = session.scalars(
            select(SourcePolicyProgramPayload)
            .where(
                SourcePolicyProgramPayload.program_id == parent.id,
                SourcePolicyProgramPayload.kind == "generated",
            )
            .order_by(SourcePolicyProgramPayload.ordinal)
            .limit(13)
        ).all()
        earlier = [
            value
            for value in old_refs
            if value.derivation.get("round_index", 0) < generated_link.round_index
        ]
        from cutback.core.policy_execution import PolicyExecutionRef

        previous = []
        for value in earlier:
            _, decoded = load_policies(
                session,
                store,
                parent,
                specification,
                [PolicyExecutionRef.model_validate(value.derivation["reference"])],
            )
            previous.append(decoded[value.derivation["reference"]["key"]])
        decoded = decode_policy_proposals(
            output["output"], specification, static, generated_link.round_index, previous=previous
        )
        if output.get("complete") is not True or decoded.get(selected.key) != proposed:
            raise ValueError("POLICY_PROGRAM_SELECTED_GENERATED_CONFIGURATION_CHANGED")
    elif owned.kind == "learned":
        from cutback.server.policy_program_content import read_content
        from cutback.server.policy_training import fit_policy

        fit_id = UUID(owned.derivation["fit_id"])
        training_id = UUID(owned.derivation["training_run_id"])
        if fit_id != parent.fit_id or specification.fit is None:
            raise ValueError("POLICY_PROGRAM_CURRENT_FIT_DERIVATION_REQUIRED")
        _, policy, _, _ = load_program(session, store, parent)
        private_fit = read_content(
            session, store, parent, fit_id, owned.derivation["fit_sha256"], policy, kind="fit"
        )
        training = session.get(ExperimentRunRecord, training_id)
        owner, training_link = program_for_run(session, training) if training else (None, None)
        if (
            owner is None
            or training_link is None
            or owner.id != parent.id
            or training_link.phase != "training"
        ):
            raise ValueError("POLICY_PROGRAM_CURRENT_TRAINING_OWNER_REQUIRED")
        fitted, refreshed_fit, _, _, _ = fit_policy(
            session, store, parent.project_id, training_id, specification.fit
        )
        plain = proposed.model_copy(update={"cache_execution": None})
        if (
            plain.behavior_sha256 != fitted.behavior_sha256
            or private_fit["policy_sha256"] != refreshed_fit["policy_sha256"]
            or private_fit["training_run_id"] != str(training_id)
        ):
            raise ValueError("POLICY_PROGRAM_CURRENT_NATIVE_FIT_CHANGED")
    strong = policy_confirmation_gate(report, plan)["passed"]
    return SourcePolicyProposal(
        program_id=parent.id,
        program_hash=parent.approval_sha256,
        run_id=run.id,
        plan_hash=row.specification["plan_hash"],
        dataset_hash=plan.dataset_hash,
        selection_hash=selection.selection_sha256,
        binding=original.source,
        original=policies[execution.reference.key],
        proposed=proposed,
        decision="synthetic_local_review_only"
        if dataset.provenance["source_type"] == "synthetic_fixture"
        else "heldout_review_required"
        if strong
        else "sample_review_required",
        optimization_known_modeled_usd=parent.spent_usd,
    )
