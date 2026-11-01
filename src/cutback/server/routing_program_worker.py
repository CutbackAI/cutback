"""Idempotent metadata/CPU transitions; the canonical worker alone owns native calls."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, or_, select

from cutback.core.input_routing import (
    SOURCE_DATASET_CASE_BOUND,
    SOURCE_DATASET_CONTENT_BOUND,
    LearnedRoutingBinding,
)
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary
from cutback.evals.routing_programs import (
    program_confirmation_gate,
    program_static_plan,
    selected_controls,
)
from cutback.evals.search import sample_frontier
from cutback.server.campaign_content import content_transaction
from cutback.server.dataset_lineage import lock_lineage, reserve_evaluation, try_lock_lineage
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.plan_artifacts import freeze_plan, load_plan
from cutback.server.routing_program_db import (
    RoutingProgramRecord,
    RoutingProgramRun,
    RoutingProgramSelection,
)
from cutback.server.routing_program_service import (
    ACTIVE,
    PHASE_STATUS,
    authorize_program,
    program_for_run,
)
from cutback.server.routing_service import fit_training, read_fit
from cutback.server.stages import validate_stage
from cutback.server.worker import load_dataset


def remaining_wall(parent):
    seconds = int((parent.deadline_at - datetime.now(UTC)).total_seconds())
    if seconds < 1:
        raise ValueError("ROUTING_PROGRAM_WALL_BUDGET_EXHAUSTED")
    return min(seconds, 3600)


def child_budget(parent):
    return Budget(
        max_usd=parent.economic_envelope["effective_max_usd"],
        max_requests=parent.economic_envelope["max_requests"],
        max_wall_seconds=remaining_wall(parent),
    )


def bound_plan(parent, raw, dataset):
    raw = {**raw, "budget": child_budget(parent).model_dump(mode="json")}
    plan = ExperimentPlan.model_validate(raw)
    summary = plan_summary(plan, dataset)
    with localcontext(Context(prec=80)):
        if (
            not summary["fits_budget"]
            or parent.requests_used + summary["requests"] > parent.economic_envelope["max_requests"]
            or parent.reserved_usd + Decimal(summary["worst_case_modeled_usd"])
            > Decimal(parent.economic_envelope["effective_max_usd"])
        ):
            raise ValueError("ROUTING_PROGRAM_DERIVED_PLAN_EXCEEDS_SHARED_ENVELOPE")
    raw["budget"].update(
        max_usd=summary["worst_case_modeled_usd"], max_requests=summary["requests"]
    )
    return ExperimentPlan.model_validate(raw)


def queue_child(session, store, parent, plan, phase, policy, refs, *, derivation=None):
    if session.scalar(
        select(RoutingProgramRun.id).where(
            RoutingProgramRun.program_id == parent.id, RoutingProgramRun.phase == phase
        )
    ):
        raise ValueError("ROUTING_PROGRAM_PHASE_ALREADY_FROZEN")
    if (
        session.scalar(
            select(func.count())
            .select_from(ExperimentPlanRecord)
            .where(ExperimentPlanRecord.project_id == parent.project_id)
        )
        >= 1000
    ):
        raise ValueError("ROUTING_PROGRAM_PLAN_QUOTA_EXHAUSTED")
    metadata = freeze_plan(store, parent.project_id, plan, policy, parent.policy_id)
    refs.append(metadata["artifact_ref"])
    metadata.update(
        expires_at=parent.expires_at.isoformat(),
        routing_program_id=str(parent.id),
        routing_program_sha256=parent.approval_sha256,
    )
    record = ExperimentPlanRecord(
        project_id=parent.project_id,
        dataset_id=parent.dataset_id,
        specification=metadata,
        approval_status="consumed",
        approved_by=parent.approved_by,
    )
    session.add(record)
    session.flush()
    run = ExperimentRunRecord(
        project_id=parent.project_id,
        plan_id=record.id,
        status="QUEUED",
        results={},
        call_ledger_version=1,
        known_cost_requests=0,
    )
    session.add(run)
    session.flush()
    validate_stage(session, store, parent.project_id, plan, run_id=run.id)
    run.results = {
        "lineage": reserve_evaluation(session, parent.project_id, parent.dataset_id, plan, run.id)
    }
    session.add(JobRecord(project_id=parent.project_id, run_id=run.id, status="QUEUED"))
    session.add(
        RoutingProgramRun(
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            program_id=parent.id,
            run_id=run.id,
            phase=phase,
            program_sha256=parent.approval_sha256,
            plan_sha256=metadata["plan_hash"],
            derivation=derivation or {},
        )
    )
    parent.status = PHASE_STATUS[phase]
    return run


def eligible_choice(report, plan):
    by_key = {model.key: model for model in plan.candidates}
    choices = [
        (Decimal(row["cost_per_request_usd"]), row["key"])
        for row in sample_frontier(report)["candidates"]
        if row.get("eligible") is True
        and row["key"] in by_key
        and row.get("cost_per_request_usd") is not None
    ]
    return by_key[min(choices)[1]] if choices else None


def freeze_selection_and_confirm(
    session, store, parent, child, prior, candidate, strategy, dataset, policy, refs
):
    configuration_hash = digest(candidate.model_dump(mode="json"))
    selected_hash = digest(
        {
            "contract": "cutback.routing-program-selection.v1",
            "program_hash": parent.approval_sha256,
            "validation_run_id": str(child.id),
            "validation_plan_hash": digest(prior.model_dump(mode="json")),
            "configuration_hash": configuration_hash,
            "strategy": strategy,
        }
    )
    session.add(
        RoutingProgramSelection(
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            program_id=parent.id,
            validation_run_id=child.id,
            candidate_key=candidate.key,
            strategy=strategy,
            configuration_sha256=configuration_hash,
            program_sha256=parent.approval_sha256,
            selection_sha256=selected_hash,
        )
    )
    # Exactly the same name/nonce/native frame as validation. The existing stage
    # authority and persistent family ledger enforce one untouched confirmation.
    raw = prior.model_dump(mode="json")
    raw.update(
        candidates=[candidate.model_dump(mode="json")],
        split="test",
        predecessor_run_id=str(child.id),
    )
    plan = bound_plan(parent, raw, dataset)
    queue_child(
        session,
        store,
        parent,
        plan,
        "confirmation",
        policy,
        refs,
        derivation={"validation_run_id": str(child.id), "selection_sha256": selected_hash},
    )
    parent.summary = {
        **parent.summary,
        "selected_strategy": strategy,
        "selected_candidate_key": candidate.key,
        "selection_sha256": selected_hash,
        "decision": "confirm_one_frozen_candidate_on_untouched_cases",
    }


def fail_parent(session, parent, code, *, status="FAILED"):
    parent.status, parent.completed_at = status, datetime.now(UTC)
    parent.lease_token = parent.lease_until = None
    parent.summary = {
        **parent.summary,
        "code": code,
        "deployment_authorized": False,
        "unsettled_reservations": parent.requests_used - parent.known_cost_requests,
    }
    for run in session.scalars(
        select(ExperimentRunRecord)
        .join(RoutingProgramRun, RoutingProgramRun.run_id == ExperimentRunRecord.id)
        .where(RoutingProgramRun.program_id == parent.id)
        .with_for_update(of=ExperimentRunRecord)
    ):
        if run.status in {"QUEUED", "BLOCKED", "RUNNING"}:
            run.status = "CANCELLED"
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == run.id).with_for_update()
            )
            if job:
                job.status = "CANCELLED"


def advance_one(
    sessions, store, *, execution_enabled=False, only_program_id=None, synthetic_transport=False
):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    now = datetime.now(UTC)
    if only_program_id is None:
        # One oldest eligible parent per project: a busy project's backlog must
        # not occupy every slot in the bounded dispatcher page.
        with sessions() as reader:
            ranked = (
                select(
                    RoutingProgramRecord.id.label("id"),
                    RoutingProgramRecord.created_at.label("created"),
                    func.row_number()
                    .over(
                        partition_by=RoutingProgramRecord.project_id,
                        order_by=(
                            RoutingProgramRecord.available_at.asc().nullsfirst(),
                            RoutingProgramRecord.created_at,
                            RoutingProgramRecord.id,
                        ),
                    )
                    .label("rank"),
                )
                .where(
                    RoutingProgramRecord.status.in_(ACTIVE),
                    or_(
                        RoutingProgramRecord.available_at.is_(None),
                        RoutingProgramRecord.available_at <= now,
                    ),
                )
                .subquery()
            )
            candidates = reader.scalars(
                select(ranked.c.id)
                .where(ranked.c.rank == 1)
                .order_by(ranked.c.created, ranked.c.id)
                .limit(16)
            ).all()
        for identifier in candidates:
            result = advance_one(
                sessions,
                store,
                execution_enabled=True,
                only_program_id=identifier,
                synthetic_transport=synthetic_transport,
            )
            if result["status"] != "IDLE":
                return result
        return {"status": "IDLE", "provider_calls": 0}
    with sessions() as reader:
        query = select(RoutingProgramRecord.id, RoutingProgramRecord.project_id).where(
            RoutingProgramRecord.status.in_(ACTIVE),
            or_(
                RoutingProgramRecord.available_at.is_(None),
                RoutingProgramRecord.available_at <= now,
            ),
        )
        if only_program_id:
            query = query.where(RoutingProgramRecord.id == UUID(str(only_program_id)))
        candidate = reader.execute(
            query.order_by(
                RoutingProgramRecord.available_at.asc().nullsfirst(),
                RoutingProgramRecord.created_at,
                RoutingProgramRecord.id,
            ).limit(1)
        ).first()
    if candidate is None:
        return {"status": "IDLE", "provider_calls": 0}
    parent_id, project = candidate
    try:
        with content_transaction(sessions, store, project) as (session, refs):
            if not try_lock_lineage(session, project):
                return {"status": "IDLE", "provider_calls": 0}
            parent = session.get(RoutingProgramRecord, parent_id, with_for_update=True)
            if (
                not parent
                or parent.status not in ACTIVE
                or (parent.available_at and parent.available_at > datetime.now(UTC))
            ):
                return {"status": "IDLE", "provider_calls": 0}
            parent.available_at = datetime.now(UTC) + timedelta(seconds=1)
            specification, policy, dataset_row, _ = authorize_program(
                session, store, parent, synthetic_transport=synthetic_transport
            )
            dataset, _ = load_dataset(
                session,
                dataset_row,
                store,
                max_cases=SOURCE_DATASET_CASE_BOUND,
                max_content_bytes=SOURCE_DATASET_CONTENT_BOUND,
            )
            controls = selected_controls(specification, parent.economic_envelope)
            if parent.status == "QUEUED":
                plan = program_static_plan(
                    specification, controls, nonce=uuid4(), budget=child_budget(parent)
                )
                queue_child(
                    session,
                    store,
                    parent,
                    bound_plan(parent, plan.model_dump(mode="json"), dataset),
                    "static_validation",
                    policy,
                    refs,
                )
                parent.summary = {
                    **parent.summary,
                    "decision": "validate_native_controls_before_training",
                }
            else:
                phase = next(key for key, status in PHASE_STATUS.items() if status == parent.status)
                link = session.scalar(
                    select(RoutingProgramRun).where(
                        RoutingProgramRun.program_id == parent.id, RoutingProgramRun.phase == phase
                    )
                )
                if link is None:
                    raise ValueError("ROUTING_PROGRAM_ACTIVE_CHILD_MISSING")
                child = session.get(ExperimentRunRecord, link.run_id, populate_existing=True)
                program_for_run(session, child)
                if child.status in {"QUEUED", "RUNNING", "BLOCKED"}:
                    return {
                        "status": parent.status,
                        "program_id": str(parent.id),
                        "provider_calls": 0,
                    }
                if (
                    child.status != "COMPLETED"
                    or not child.requests_used
                    or child.call_ledger_version != 1
                    or child.requests_used != child.known_cost_requests
                    or child.results.get("billing_complete") is not True
                    or child.results.get("plan", {}).get("plan_hash") != link.plan_sha256
                ):
                    raise ValueError("ROUTING_PROGRAM_CHILD_FAILED_OR_BILLING_UNCERTAIN_NO_REPLAY")
                child_row = session.get(ExperimentPlanRecord, child.plan_id)
                prior = load_plan(store, project, child_row.specification)
                if phase in {"static_validation", "routed_validation"}:
                    selected = eligible_choice(child.results, prior)
                    if selected:
                        freeze_selection_and_confirm(
                            session,
                            store,
                            parent,
                            child,
                            prior,
                            selected,
                            "static_native"
                            if phase == "static_validation"
                            else "learned_input_size",
                            dataset,
                            policy,
                            refs,
                        )
                    elif phase == "static_validation":
                        raw = specification.template.model_dump(mode="json")
                        raw.update(
                            candidates=[model.model_dump(mode="json") for model in controls],
                            content_nonce=str(uuid4()),
                        )
                        queue_child(
                            session,
                            store,
                            parent,
                            bound_plan(parent, raw, dataset),
                            "training",
                            policy,
                            refs,
                        )
                        parent.summary = {
                            **parent.summary,
                            "decision": "native_controls_failed_use_reserved_training",
                        }
                    else:
                        parent.status, parent.completed_at = "COMPLETED", datetime.now(UTC)
                        parent.summary = {
                            **parent.summary,
                            "code": "no_eligible_routing_candidate",
                            "decision": "reject_preserve_source_configuration",
                            "deployment_authorized": False,
                        }
                elif phase == "training":
                    # CPU fit is repeatable, native training is not. A unique phase
                    # and deterministic artifact identity reconcile a lost commit reply.
                    if remaining_wall(parent) <= specification.fit.max_fit_seconds:
                        raise ValueError("ROUTING_PROGRAM_FIT_WALL_ALLOCATION_EXHAUSTED")
                    sample = fit_training(
                        session,
                        store,
                        project,
                        child.id,
                        specification.fit,
                        identifier=uuid5(parent.id, "routing-program-fit-v1"),
                        request_hash=digest(
                            {
                                "program_hash": parent.approval_sha256,
                                "training_run_id": str(child.id),
                                "options": specification.fit.model_dump(mode="json"),
                            }
                        ),
                        actor=parent.approved_by,
                        refs=refs,
                    )
                    remaining_wall(parent)
                    sample.expires_at = min(sample.expires_at, parent.expires_at)
                    sample.metadata_safe = {
                        **sample.metadata_safe,
                        "routing_program_id": str(parent.id),
                        "routing_program_sha256": parent.approval_sha256,
                    }
                    parent.fit_id = sample.id
                    _, fitted, training, _ = read_fit(session, store, project, sample.id)
                    raw = training.model_dump(mode="json")
                    raw.update(
                        split="validation",
                        candidates=[fitted["fit"]["candidate"]],
                        content_nonce=str(uuid4()),
                        routing_execution=LearnedRoutingBinding(
                            fit_id=sample.id,
                            fit_sha256=sample.content_hash,
                            training_run_id=child.id,
                            transport=training.routing_execution.transport,
                            source=training.routing_execution.source,
                        ).model_dump(mode="json"),
                    )
                    queue_child(
                        session,
                        store,
                        parent,
                        bound_plan(parent, raw, dataset),
                        "routed_validation",
                        policy,
                        refs,
                        derivation={"fit_id": str(sample.id)},
                    )
                else:
                    selection = session.scalar(
                        select(RoutingProgramSelection).where(
                            RoutingProgramSelection.program_id == parent.id
                        )
                    )
                    if (
                        not selection
                        or selection.program_sha256 != parent.approval_sha256
                        or selection.configuration_sha256
                        != digest(prior.candidates[0].model_dump(mode="json"))
                        or str(selection.validation_run_id) != str(prior.predecessor_run_id)
                        or link.derivation.get("selection_sha256") != selection.selection_sha256
                    ):
                        raise ValueError("ROUTING_PROGRAM_FROZEN_SELECTION_CHANGED")
                    gate = program_confirmation_gate(child.results, prior)
                    sample_gate = program_confirmation_gate(child.results, prior, sample_only=True)
                    parent.status, parent.completed_at = "COMPLETED", datetime.now(UTC)
                    parent.summary = {
                        **parent.summary,
                        "code": "confirmation_completed",
                        "confirmation_gate": gate,
                        "sample_only_gate": sample_gate,
                        "confirmation_run_id": str(child.id),
                        "decision": "prepare_application_checks"
                        if gate["passed"]
                        else "synthetic_confirmation_passed_real_data_required"
                        if dataset_row.provenance.get("source_type") == "synthetic_fixture"
                        and sample_gate["passed"]
                        else "confirmation_requires_review_or_rejected",
                        "native_billing_complete": parent.requests_used
                        == parent.known_cost_requests,
                        "fit_compute_usd": None,
                        "routing_hosting_usd": None,
                        "production_savings_usd": None,
                        "deployment_authorized": False,
                    }
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="routing_program_worker",
                    action="routing_program_advanced",
                    details={"program_id": str(parent.id), "status": parent.status},
                )
            )
            return {"status": parent.status, "program_id": str(parent.id), "provider_calls": 0}
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError else ""
        if not re.fullmatch(r"[A-Z_]{3,110}", code):
            code = "ROUTING_PROGRAM_TRANSITION_FAILED_NO_BILLABLE_REPLAY"
        with sessions.begin() as session:
            lock_lineage(session, project)
            parent = session.get(RoutingProgramRecord, parent_id, with_for_update=True)
            if parent and parent.status in ACTIVE:
                fail_parent(session, parent, code)
        return {"status": "FAILED", "program_id": str(parent_id), "provider_calls": 0}
