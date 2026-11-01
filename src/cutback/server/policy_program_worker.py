"""Common-policy orchestration; provider requests run only in canonical child workers."""

import math
import re
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
from uuid import UUID, uuid5

from sqlalchemy import func, or_, select

from cutback.core.policy_execution import PolicyExecutionRef, SourcePolicyExecutionSpec
from cutback.evals.campaigns import proposal_model
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary, upper_cost
from cutback.evals.policy_artifacts import decode_policy_bundle
from cutback.evals.policy_generation import (
    PolicyGenerationFeedback,
    PolicyGenerationPlan,
    decode_policy_proposals,
    generation_input,
)
from cutback.evals.policy_models import policy_descriptors
from cutback.evals.search import sample_frontier
from cutback.evals.source_policy_programs import policy_confirmation_gate
from cutback.server.campaign_content import content_transaction
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import (
    expose,
    lock_lineage,
    reserve_evaluation,
    try_lock_lineage,
)
from cutback.server.db import (
    AuditEvent,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    PayloadSample,
)
from cutback.server.plan_artifacts import freeze_plan, load_plan, public_config
from cutback.server.policy_program_content import put_content, read_content
from cutback.server.policy_program_db import (
    SourcePolicyProgramPayload,
    SourcePolicyProgramRecord,
    SourcePolicyProgramRun,
    SourcePolicyProgramSelection,
)
from cutback.server.policy_program_service import (
    ACTIVE,
    PHASE_STATUS,
    authorize_program,
    link_payload,
    program_for_run,
    require_link,
)
from cutback.server.policy_service import execution_payloads, put_policy
from cutback.server.stages import validate_stage
from cutback.server.worker import load_dataset


def remaining_wall(parent):
    seconds = (parent.deadline_at - datetime.now(UTC)).total_seconds()
    if seconds <= 0:
        raise ValueError("POLICY_PROGRAM_WALL_BUDGET_EXHAUSTED")
    return min(3600, math.ceil(seconds))


def child_budget(parent):
    with localcontext(Context(prec=80)):
        dollars = (
            Decimal(parent.economic_envelope["worst_case_native_and_cache_usd"])
            - parent.reserved_usd
        )
        requests = parent.economic_envelope["max_requests"] - parent.requests_used
    if dollars < 0 or requests < 1:
        raise ValueError("POLICY_PROGRAM_SHARED_BUDGET_EXHAUSTED")
    return Budget(max_usd=dollars, max_requests=requests, max_wall_seconds=remaining_wall(parent))


def bound_plan(parent, raw, dataset):
    raw = {**raw, "budget": child_budget(parent).model_dump(mode="json")}
    plan = ExperimentPlan.model_validate(raw)
    summary = plan_summary(plan, dataset)
    if not summary["fits_budget"]:
        raise ValueError("POLICY_PROGRAM_DERIVED_PLAN_EXCEEDS_SHARED_ENVELOPE")
    raw["budget"].update(
        max_usd=summary["worst_case_modeled_usd"], max_requests=summary["requests"]
    )
    return ExperimentPlan.model_validate(raw)


def owned_references(session, parent, *, kinds):
    rows = session.scalars(
        select(SourcePolicyProgramPayload)
        .where(
            SourcePolicyProgramPayload.program_id == parent.id,
            SourcePolicyProgramPayload.kind.in_(kinds),
        )
        .order_by(SourcePolicyProgramPayload.ordinal)
        .limit(66)
    ).all()
    if len(rows) > 65:
        raise ValueError("POLICY_PROGRAM_CONTENT_DERIVATION_BOUND")
    result = []
    for row in rows:
        reference = PolicyExecutionRef.model_validate(row.derivation.get("reference"))
        if (
            row.kind == "static"
            and reference.key not in parent.economic_envelope["selected_control_keys"]
        ):
            continue
        require_link(session, parent, reference.payload_id, reference.payload_sha256, kinds=kinds)
        result.append(reference)
    return result


def load_policies(session, store, parent, specification, references, *, training=False):
    original = specification.template.source_policy_execution
    if original is None or not references:
        raise ValueError("POLICY_PROGRAM_BOUNDED_POLICY_REFERENCES_REQUIRED")
    raw = original.model_dump(mode="json")
    raw.update(
        candidates=[value.model_dump(mode="json") for value in references],
        phase="training" if training else "evaluation",
        max_cases=min(original.max_cases, 256) if training else original.max_cases,
    )
    execution = SourcePolicyExecutionSpec.model_validate(raw)
    for reference in [execution.reference, *execution.candidates]:
        link = require_link(
            session,
            parent,
            reference.payload_id,
            reference.payload_sha256,
            kinds={"reference", "static", "generated", "learned"},
        )
        if link.derivation.get("reference") != reference.model_dump(mode="json"):
            raise ValueError("POLICY_PROGRAM_FROZEN_POLICY_DERIVATION_CHANGED")
    payloads = execution_payloads(session, store, parent.project_id, execution, parent.dataset_id)
    return execution, decode_policy_bundle(payloads, execution)


def all_policies(session, store, parent, specification, *, kinds):
    references = owned_references(session, parent, kinds=kinds)
    policies = {}
    for start in range(0, len(references), 5):
        _, values = load_policies(
            session, store, parent, specification, references[start : start + 5]
        )
        policies.update(values)
    return references, policies


def plan_for_refs(session, store, parent, specification, references, dataset, *, training=False):
    execution, policies = load_policies(
        session, store, parent, specification, references, training=training
    )
    models = policy_descriptors(execution, policies)
    raw = specification.template.model_dump(mode="json")
    raw.update(
        baseline=models[execution.reference.key].model_dump(mode="json"),
        candidates=[models[value.key].model_dump(mode="json") for value in references],
        source_policy_execution=execution.model_dump(mode="json"),
        split="train" if training else "validation",
        predecessor_run_id=None,
    )
    return bound_plan(parent, raw, dataset)


def queue_child(
    session, store, parent, plan, phase, policy, refs, *, derivation=None, round_index=0
):
    if session.scalar(
        select(SourcePolicyProgramRun.id).where(
            SourcePolicyProgramRun.program_id == parent.id,
            SourcePolicyProgramRun.phase == phase,
            SourcePolicyProgramRun.round_index == round_index,
        )
    ):
        raise ValueError("POLICY_PROGRAM_PHASE_ALREADY_FROZEN_NO_REPLAY")
    if (
        session.scalar(
            select(func.count())
            .select_from(ExperimentPlanRecord)
            .where(
                ExperimentPlanRecord.project_id == parent.project_id,
            )
        )
        >= 1000
    ):
        raise ValueError("POLICY_PROGRAM_SHARED_PLAN_QUOTA_EXHAUSTED")
    if isinstance(plan, ExperimentPlan):
        metadata = freeze_plan(store, parent.project_id, plan, policy, parent.policy_id)
    else:
        raw = plan.model_dump(mode="json")
        _, blob = policy_content(raw, policy, preserve_semantics=True)
        if len(blob) > 512000:
            raise ValueError("POLICY_PROGRAM_PRIVATE_PLAN_BYTE_BOUND")
        artifact = store.put(parent.project_id, blob, policy.retention_days)
        metadata = {
            "public_specification": public_config(raw, redact_content_hashes=True),
            "plan_hash": digest(raw),
            "artifact_ref": artifact,
            "policy_id": str(parent.policy_id),
            "expires_at": parent.expires_at.isoformat(),
        }
    refs.append(metadata["artifact_ref"])
    metadata.update(
        expires_at=min(
            parent.expires_at, datetime.fromisoformat(metadata["expires_at"])
        ).isoformat(),
        source_policy_program_id=str(parent.id),
        source_policy_program_sha256=parent.approval_sha256,
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
    if isinstance(plan, ExperimentPlan):
        validate_stage(session, store, parent.project_id, plan, run_id=run.id)
        run.results = {
            "lineage": reserve_evaluation(
                session, parent.project_id, parent.dataset_id, plan, run.id
            )
        }
    else:
        expose(
            session,
            parent.project_id,
            parent.dataset_id,
            "train",
            f"source-policy-program:{parent.id}:generation:{round_index}",
            run_id=run.id,
        )
    session.add(
        JobRecord(
            project_id=parent.project_id,
            run_id=run.id,
            status="QUEUED",
            execution_kind="source_policy_generation" if phase == "generation" else "evaluation",
        )
    )
    session.add(
        SourcePolicyProgramRun(
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            program_id=parent.id,
            run_id=run.id,
            phase=phase,
            round_index=round_index,
            program_sha256=parent.approval_sha256,
            plan_sha256=metadata["plan_hash"],
            derivation=derivation or {},
        )
    )
    parent.status = PHASE_STATUS[phase]
    return run


def save_policy(
    session, store, parent, specification, key, candidate, kind, policy, refs, *, derivation
):
    identifier = uuid5(parent.id, "source-policy-version:" + key)
    reference = put_policy(
        session,
        store,
        parent.project_id,
        parent.dataset_id,
        specification.template.source_policy_execution.source,
        key,
        candidate,
        identifier,
        refs,
    )
    sample = session.get(PayloadSample, identifier)
    link_payload(
        session,
        parent,
        sample,
        kind,
        derivation={"reference": reference.model_dump(mode="json"), **derivation},
    )
    return reference


def eligible_choice(report, plan):
    by_key = {value.key: value for value in plan.source_policy_execution.candidates}
    with localcontext(Context(prec=80)):
        choices = [
            (Decimal(row["cost_per_request_usd"]), row["key"])
            for row in sample_frontier(report)["candidates"]
            if row.get("eligible") is True
            and row["key"] in by_key
            and row.get("cost_per_request_usd") is not None
        ]
    return by_key[min(choices)[1]] if choices else None


def freeze_confirmation(
    session, store, parent, child, prior, selected, strategy, dataset, policy, refs
):
    selected_hash = digest(
        {
            "contract": "cutback.source-policy-selection.v1",
            "program_hash": parent.approval_sha256,
            "validation_run_id": str(child.id),
            "validation_plan_hash": digest(prior.model_dump(mode="json")),
            "selected_reference": selected.model_dump(mode="json"),
            "strategy": strategy,
        }
    )
    session.add(
        SourcePolicyProgramSelection(
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            program_id=parent.id,
            validation_run_id=child.id,
            candidate_key=selected.key,
            strategy=strategy,
            configuration_sha256=selected.policy_sha256,
            program_sha256=parent.approval_sha256,
            selection_sha256=selected_hash,
        )
    )
    raw = prior.model_dump(mode="json")
    raw.update(
        split="test",
        predecessor_run_id=str(child.id),
        candidates=[
            value.model_dump(mode="json") for value in prior.candidates if value.key == selected.key
        ],
    )
    raw["source_policy_execution"]["candidates"] = [selected.model_dump(mode="json")]
    queue_child(
        session,
        store,
        parent,
        bound_plan(parent, raw, dataset),
        "confirmation",
        policy,
        refs,
        derivation={"selection_sha256": selected_hash, "validation_run_id": str(child.id)},
    )
    parent.summary = {
        **parent.summary,
        "selected_strategy": strategy,
        "selected_candidate_key": selected.key,
        "selection_sha256": selected_hash,
        "decision": "confirm_one_frozen_policy_on_full_untouched_test",
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
        .join(
            SourcePolicyProgramRun,
            SourcePolicyProgramRun.run_id == ExperimentRunRecord.id,
        )
        .where(SourcePolicyProgramRun.program_id == parent.id)
        .with_for_update(of=ExperimentRunRecord)
    ):
        if run.status in {"QUEUED", "RUNNING", "BLOCKED"}:
            run.status = "CANCELLED"
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == run.id).with_for_update()
            )
            if job:
                job.status = "CANCELLED"


def selected_static(specification, envelope):
    original = specification.template.source_policy_execution
    keys = envelope.get("selected_control_keys")
    if (
        original is None
        or envelope.get("eligible") is not True
        or type(keys) is not list
        or not keys
        or len(keys) != len(set(keys))
        or keys != [value.key for value in original.candidates if value.key in keys]
    ):
        raise ValueError("POLICY_PROGRAM_SELECTED_STATIC_CONTROLS_CHANGED")
    return [value for value in original.candidates if value.key in keys]


def feedback_history(session, store, parent):
    latest = {}
    pairs = session.execute(
        select(SourcePolicyProgramRun, ExperimentRunRecord)
        .join(
            ExperimentRunRecord,
            SourcePolicyProgramRun.run_id == ExperimentRunRecord.id,
        )
        .where(
            SourcePolicyProgramRun.program_id == parent.id,
            SourcePolicyProgramRun.phase.in_(["static_validation", "generated_validation"]),
        )
        .order_by(SourcePolicyProgramRun.created_at, SourcePolicyProgramRun.id)
        .limit(5)
    ).all()
    if len(pairs) > 4:
        raise ValueError("POLICY_PROGRAM_VALIDATION_HISTORY_BOUND")
    for link, run in pairs:
        if (
            run.status != "COMPLETED"
            or run.results.get("billing_complete") is not True
            or run.results.get("plan", {}).get("plan_hash") != link.plan_sha256
        ):
            raise ValueError("POLICY_PROGRAM_COMPLETE_VALIDATION_FEEDBACK_REQUIRED")
        row = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_plan(store, parent.project_id, row.specification)
        decisions = {value["candidate"]["key"]: value for value in run.results["recommendations"]}
        for model in run.results.get("models", []):
            key, count = model["model"]["key"], model.get("requests")
            cost = model.get("modeled_cost_usd") if model.get("cost_complete") is True else None
            score = model.get("metric_min")
            passed = decisions.get(key, {}).get("quality_gate_passed") is True
            if key == plan.baseline.key:
                passed = (
                    score is not None
                    and score >= plan.metric.minimum
                    and all(
                        not value["required"] or value.get("sample_gate_passed") is True
                        for value in model.get("criteria", [])
                    )
                )
            latest[key] = PolicyGenerationFeedback(
                candidate_key=key,
                quality_gate_passed=passed,
                sample_metric_min=score,
                cost_complete=cost is not None and type(count) is int and count > 0,
                known_cost_subtotal_usd=cost,
                requests=count if type(count) is int and count > 0 else None,
            )
    return list(latest.values())


def queue_generation(session, store, parent, specification, dataset, policy, refs, round_index):
    static = selected_static(specification, parent.economic_envelope)
    _, policies = load_policies(session, store, parent, specification, static)
    _, previous = all_policies(session, store, parent, specification, kinds={"generated"})
    previous = [
        value
        for key, value in previous.items()
        if key != specification.template.source_policy_execution.reference.key
    ]
    text, families = generation_input(
        specification,
        dataset,
        policies,
        round_index,
        feedback=feedback_history(session, store, parent),
        previous=previous,
    )
    payload = put_content(
        session,
        store,
        parent,
        {"input": text, "training_families": families},
        "generation_input",
        policy,
        refs,
        identifier=uuid5(parent.id, f"generation-input-{round_index}"),
        derivation={"round_index": round_index},
    )
    model = proposal_model(specification.generator)
    budget = child_budget(parent)
    if upper_cost(model) > budget.max_usd:
        raise ValueError("POLICY_PROGRAM_GENERATOR_EXCEEDS_SHARED_BUDGET")
    plan = PolicyGenerationPlan(
        program_id=parent.id,
        program_sha256=parent.approval_sha256,
        round_index=round_index,
        workflow=specification.template.workflow,
        dataset_hash=specification.template.dataset_hash,
        input_payload_id=payload.id,
        input_sha256=payload.content_hash,
        baseline=model,
        budget=Budget(
            max_usd=upper_cost(model), max_requests=1, max_wall_seconds=budget.max_wall_seconds
        ),
    )
    parent.current_round = round_index
    queue_child(session, store, parent, plan, "generation", policy, refs, round_index=round_index)
    parent.summary = {**parent.summary, "decision": "use_reserved_train_only_instruction_search"}


def complete_without_choice(parent, code):
    parent.status, parent.completed_at = "COMPLETED", datetime.now(UTC)
    parent.summary = {
        **parent.summary,
        "code": code,
        "decision": "reject_preserve_source_configuration",
        "deployment_authorized": False,
        "production_savings_usd": None,
    }


def queue_training(session, store, parent, specification, dataset, policy, refs):
    if specification.fit is None:
        complete_without_choice(parent, "no_eligible_policy_without_training")
        return
    references, policies = all_policies(
        session, store, parent, specification, kinds={"static", "generated"}
    )
    feedback = {value.candidate_key: value for value in feedback_history(session, store, parent)}
    approved = {value.key for value in selected_static(specification, parent.economic_envelope)}
    choices = []
    for reference in references:
        candidate = policies[reference.key]
        observed = feedback.get(reference.key)
        if (
            reference.key not in approved
            and not reference.key.startswith("policy-generated-")
            or candidate.strategy.routes
            or candidate.cache_execution is not None
            or observed is None
            or observed.known_cost_subtotal_usd is None
            or observed.requests is None
        ):
            continue
        choices.append(
            (observed.known_cost_subtotal_usd / observed.requests, reference.key, reference)
        )
    selected = [value[2] for value in sorted(choices, key=lambda value: (value[0], value[1]))[:4]]
    if not selected:
        complete_without_choice(parent, "no_uncached_native_policies_for_reserved_training")
        return
    plan = plan_for_refs(session, store, parent, specification, selected, dataset, training=True)
    queue_child(session, store, parent, plan, "training", policy, refs)
    parent.summary = {
        **parent.summary,
        "decision": "search_failed_use_reserved_native_training_and_fit",
    }


def fit_and_validate(session, store, parent, specification, child, dataset, policy, refs):
    from cutback.server.policy_training import fit_policy

    if specification.fit is None or remaining_wall(parent) <= specification.fit.max_fit_seconds:
        raise ValueError("POLICY_PROGRAM_FIT_WALL_ALLOCATION_EXHAUSTED")
    try:
        fitted, private_fit, _, _, expiry = fit_policy(
            session, store, parent.project_id, child.id, specification.fit
        )
    except ValueError as failure:
        if str(failure) in {
            "ROUTING_TRAINING_NO_FEASIBLE_PARTITION",
            "ROUTING_TRAINING_NO_LOWER_COST_FEASIBLE_POLICY",
            "ROUTING_TRAINING_GROUPS_INSUFFICIENT",
        }:
            complete_without_choice(parent, "no_feasible_lower_cost_source_policy_fit")
            return
        raise
    remaining_wall(parent)
    fit = put_content(
        session,
        store,
        parent,
        private_fit,
        "fit",
        policy,
        refs,
        identifier=uuid5(parent.id, "source-policy-fit-v1"),
        derivation={"training_run_id": str(child.id)},
    )
    fit.expires_at = min(fit.expires_at, expiry)
    parent.fit_id = fit.id
    _, previous = all_policies(session, store, parent, specification, kinds={"static", "generated"})
    seen = {value.behavior_sha256 for value in previous.values()}
    candidates = {"policy-fit-native": fitted}
    selected = {value.key for value in selected_static(specification, parent.economic_envelope)}
    cached = [
        value
        for key, value in previous.items()
        if key in selected and value.cache_execution is not None
    ]
    if cached:
        cache = min(
            cached,
            key=lambda value: (value.cache_execution.operation_cost_upper_usd, value.policy_sha256),
        ).cache_execution
        raw = fitted.model_dump(mode="json")
        raw["cache_execution"] = cache.model_dump(mode="json")
        from cutback.core.improvement_policy import SourceImprovementPolicy

        candidates["policy-fit-cache"] = SourceImprovementPolicy.model_validate(raw)
    frozen = []
    for key, candidate in candidates.items():
        if candidate.behavior_sha256 in seen:
            continue
        seen.add(candidate.behavior_sha256)
        frozen.append(
            save_policy(
                session,
                store,
                parent,
                specification,
                key,
                candidate,
                "learned",
                policy,
                refs,
                derivation={
                    "fit_id": str(fit.id),
                    "fit_sha256": fit.content_hash,
                    "training_run_id": str(child.id),
                },
            )
        )
    if not frozen:
        complete_without_choice(parent, "fitted_policy_duplicates_a_previously_tested_control")
        return
    plan = plan_for_refs(session, store, parent, specification, frozen, dataset)
    queue_child(
        session,
        store,
        parent,
        plan,
        "routed_validation",
        policy,
        refs,
        derivation={"fit_id": str(fit.id), "fit_sha256": fit.content_hash},
    )


def advance_one(
    sessions, store, *, execution_enabled=False, only_program_id=None, synthetic_transport=False
):
    with localcontext(Context(prec=80)):
        return _advance_one(
            sessions,
            store,
            execution_enabled=execution_enabled,
            only_program_id=only_program_id,
            synthetic_transport=synthetic_transport,
        )


def _advance_one(sessions, store, *, execution_enabled, only_program_id, synthetic_transport):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    now = datetime.now(UTC)
    if only_program_id is None:
        with sessions() as reader:
            ranked = (
                select(
                    SourcePolicyProgramRecord.id,
                    SourcePolicyProgramRecord.created_at,
                    func.row_number()
                    .over(
                        partition_by=SourcePolicyProgramRecord.project_id,
                        order_by=(
                            SourcePolicyProgramRecord.available_at.asc().nullsfirst(),
                            SourcePolicyProgramRecord.created_at,
                            SourcePolicyProgramRecord.id,
                        ),
                    )
                    .label("rank"),
                )
                .where(
                    SourcePolicyProgramRecord.status.in_(ACTIVE),
                    or_(
                        SourcePolicyProgramRecord.available_at.is_(None),
                        SourcePolicyProgramRecord.available_at <= now,
                    ),
                )
                .subquery()
            )
            candidates = reader.scalars(
                select(ranked.c.id)
                .where(ranked.c.rank == 1)
                .order_by(ranked.c.created_at, ranked.c.id)
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
            if result["status"] not in {"IDLE", "SYNTHETIC_EXECUTOR_REQUIRED"}:
                return result
        return {"status": "IDLE", "provider_calls": 0}
    with sessions() as reader:
        found = reader.execute(
            select(SourcePolicyProgramRecord.id, SourcePolicyProgramRecord.project_id).where(
                SourcePolicyProgramRecord.id == UUID(str(only_program_id)),
                SourcePolicyProgramRecord.status.in_(ACTIVE),
                or_(
                    SourcePolicyProgramRecord.available_at.is_(None),
                    SourcePolicyProgramRecord.available_at <= now,
                ),
            )
        ).first()
    if found is None:
        return {"status": "IDLE", "provider_calls": 0}
    identifier, project = found
    try:
        with content_transaction(sessions, store, project) as (session, refs):
            if not try_lock_lineage(session, project):
                return {"status": "IDLE", "provider_calls": 0}
            parent = session.get(SourcePolicyProgramRecord, identifier, with_for_update=True)
            if (
                not parent
                or parent.status not in ACTIVE
                or parent.available_at is not None
                and parent.available_at > datetime.now(UTC)
            ):
                return {"status": "IDLE", "provider_calls": 0}
            parent.available_at = datetime.now(UTC) + timedelta(seconds=1)
            if (
                parent.specification["template"]["source_policy_execution"]["transport"]
                == "scripted_test_only"
                and synthetic_transport is not True
            ):
                # An ordinary worker may inspect a fixture queue. It cannot execute
                # or consume it, and must not strand other tenants behind it.
                return {
                    "status": "SYNTHETIC_EXECUTOR_REQUIRED",
                    "program_id": str(parent.id),
                    "provider_calls": 0,
                }
            specification, policy, dataset_row, _ = authorize_program(
                session,
                store,
                parent,
                synthetic_transport=synthetic_transport,
            )
            dataset, _ = load_dataset(
                session, dataset_row, store, max_cases=1024, max_content_bytes=16 * 1048576
            )
            if parent.status == "QUEUED":
                plan = plan_for_refs(
                    session,
                    store,
                    parent,
                    specification,
                    selected_static(specification, parent.economic_envelope),
                    dataset,
                )
                queue_child(session, store, parent, plan, "static_validation", policy, refs)
                parent.summary = {
                    **parent.summary,
                    "decision": "validate_simple_policies_before_search_or_training",
                }
            else:
                phase = next(key for key, status in PHASE_STATUS.items() if status == parent.status)
                round_index = (
                    parent.current_round if phase in {"generation", "generated_validation"} else 0
                )
                link = session.scalar(
                    select(SourcePolicyProgramRun).where(
                        SourcePolicyProgramRun.program_id == parent.id,
                        SourcePolicyProgramRun.phase == phase,
                        SourcePolicyProgramRun.round_index == round_index,
                    )
                )
                if link is None:
                    raise ValueError("POLICY_PROGRAM_ACTIVE_CHILD_MISSING")
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
                ):
                    raise ValueError(
                        "POLICY_PROGRAM_CHILD_FAILED_OR_ACCOUNTING_UNCERTAIN_NO_REPLAY"
                    )
                row = session.get(ExperimentPlanRecord, child.plan_id)
                if phase == "generation":
                    from cutback.server.policy_generation_worker import load_generation

                    generated = load_generation(store, project, row.specification)
                    if (
                        child.requests_used != 1
                        or child.results.get("operation") != generated.operation
                        or generated.program_id != parent.id
                        or generated.round_index != round_index
                    ):
                        raise ValueError("POLICY_PROGRAM_GENERATION_RECEIPT_CHANGED")
                    output = read_content(
                        session,
                        store,
                        parent,
                        UUID(child.results["output_payload_id"]),
                        child.results["output_sha256"],
                        policy,
                        kind="generation_output",
                    )
                    if (
                        type(output) is not dict
                        or set(output) != {"output", "complete"}
                        or output["complete"] is not True
                    ):
                        raise ValueError("POLICY_PROGRAM_COMPLETE_GENERATION_OUTPUT_REQUIRED")
                    _, static = load_policies(
                        session,
                        store,
                        parent,
                        specification,
                        selected_static(specification, parent.economic_envelope),
                    )
                    _, previous = all_policies(
                        session, store, parent, specification, kinds={"generated"}
                    )
                    original_key = specification.template.source_policy_execution.reference.key
                    previous = [value for key, value in previous.items() if key != original_key]
                    candidates = decode_policy_proposals(
                        output["output"], specification, static, round_index, previous=previous
                    )
                    frozen = [
                        save_policy(
                            session,
                            store,
                            parent,
                            specification,
                            key,
                            candidate,
                            "generated",
                            policy,
                            refs,
                            derivation={
                                "generation_run_id": str(child.id),
                                "round_index": round_index,
                            },
                        )
                        for key, candidate in candidates.items()
                    ]
                    plan = plan_for_refs(session, store, parent, specification, frozen, dataset)
                    queue_child(
                        session,
                        store,
                        parent,
                        plan,
                        "generated_validation",
                        policy,
                        refs,
                        round_index=round_index,
                        derivation={"generation_run_id": str(child.id)},
                    )
                    parent.summary = {
                        **parent.summary,
                        "decision": "validate_frozen_generated_instructions",
                    }
                else:
                    if child.results.get("plan", {}).get("plan_hash") != link.plan_sha256:
                        raise ValueError("POLICY_PROGRAM_CHILD_REPORT_PLAN_CHANGED")
                    prior = load_plan(store, project, row.specification)
                    if phase in {"static_validation", "generated_validation", "routed_validation"}:
                        selected = eligible_choice(child.results, prior)
                        if selected is not None:
                            freeze_confirmation(
                                session,
                                store,
                                parent,
                                child,
                                prior,
                                selected,
                                "static"
                                if phase == "static_validation"
                                else "generated"
                                if phase == "generated_validation"
                                else "learned",
                                dataset,
                                policy,
                                refs,
                            )
                        elif phase == "static_validation" and specification.generator is not None:
                            queue_generation(
                                session, store, parent, specification, dataset, policy, refs, 1
                            )
                        elif (
                            phase == "generated_validation"
                            and parent.current_round < specification.generator.rounds
                        ):
                            queue_generation(
                                session,
                                store,
                                parent,
                                specification,
                                dataset,
                                policy,
                                refs,
                                parent.current_round + 1,
                            )
                        elif phase in {"static_validation", "generated_validation"}:
                            queue_training(
                                session, store, parent, specification, dataset, policy, refs
                            )
                        else:
                            complete_without_choice(parent, "no_eligible_fitted_source_policy")
                    elif phase == "training":
                        fit_and_validate(
                            session, store, parent, specification, child, dataset, policy, refs
                        )
                    else:
                        selection = session.scalar(
                            select(SourcePolicyProgramSelection).where(
                                SourcePolicyProgramSelection.program_id == parent.id,
                            )
                        )
                        selected = prior.source_policy_execution.candidates[0]
                        if (
                            not selection
                            or selection.program_sha256 != parent.approval_sha256
                            or selection.configuration_sha256 != selected.policy_sha256
                            or selection.candidate_key != selected.key
                            or str(selection.validation_run_id) != prior.predecessor_run_id
                            or link.derivation.get("selection_sha256") != selection.selection_sha256
                        ):
                            raise ValueError("POLICY_PROGRAM_FROZEN_SELECTION_CHANGED")
                        gate = policy_confirmation_gate(child.results, prior)
                        sample_gate = policy_confirmation_gate(
                            child.results, prior, sample_only=True
                        )
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
                            "modeled_billing_complete": parent.requests_used
                            == parent.known_cost_requests,
                            "invoice_verified": False,
                            "fit_compute_usd": None,
                            "routing_hosting_usd": None,
                            "production_savings_usd": None,
                            "deployment_authorized": False,
                        }
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="source_policy_program_worker",
                    action="source_policy_program_advanced",
                    details={"program_id": str(parent.id), "status": parent.status},
                )
            )
            return {"status": parent.status, "program_id": str(parent.id), "provider_calls": 0}
    except Exception as failure:
        code = str(failure) if type(failure) is ValueError else ""
        if not re.fullmatch(r"[A-Z_]{3,110}", code):
            code = "POLICY_PROGRAM_TRANSITION_FAILED_NO_BILLABLE_REPLAY"
        with sessions.begin() as session:
            lock_lineage(session, project)
            parent = session.get(SourcePolicyProgramRecord, identifier, with_for_update=True)
            if parent and parent.status in ACTIVE:
                expired = (
                    parent.expires_at <= datetime.now(UTC)
                    or parent.deadline_at is not None
                    and parent.deadline_at <= datetime.now(UTC)
                )
                fail_parent(session, parent, code, status="EXPIRED" if expired else "FAILED")
            status = parent.status if parent else "FAILED"
        return {"status": status, "program_id": str(identifier), "provider_calls": 0}
