"""Bounded adaptive search orchestration; native generation uses a separate worker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select

from cutback.evals.campaigns import (
    GenerationFeedback,
    ProposalOnlyPlan,
    decode_proposals,
    generation_input,
    proposal_model,
)
from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, ExperimentPlan, plan_summary, upper_cost
from cutback.server.campaign_api import training_lineage
from cutback.server.campaign_content import content_transaction, put_payload, read_payload
from cutback.server.campaign_db import (
    CampaignCandidate,
    CampaignRecord,
    CampaignRun,
    CampaignSelection,
)
from cutback.server.campaign_service import authorize_campaign, candidate_model
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import expose, lock_lineage, reserve_evaluation
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.plan_artifacts import freeze_plan, load_plan, public_config
from cutback.server.worker import load_dataset

WAITING = {"WAITING_PROPOSAL", "WAITING_VALIDATION", "WAITING_CONFIRMATION"}


def remaining_wall(parent):
    seconds = int((parent.deadline_at - datetime.now(UTC)).total_seconds())
    if seconds < 1:
        raise ValueError("CAMPAIGN_WALL_BUDGET_EXHAUSTED")
    return min(seconds, 3600)


def freeze_raw(session, store, parent, plan, policy, refs):
    if (
        session.scalar(
            select(func.count())
            .select_from(ExperimentPlanRecord)
            .where(ExperimentPlanRecord.project_id == parent.project_id)
        )
        >= 1000
    ):
        raise ValueError("CAMPAIGN_PROJECT_PLAN_QUOTA_EXHAUSTED")
    if isinstance(plan, ExperimentPlan):
        metadata = freeze_plan(store, parent.project_id, plan, policy, parent.policy_id)
    else:
        raw = plan.model_dump(mode="json")
        _, blob = policy_content(raw, policy, preserve_semantics=True)
        ref = store.put(parent.project_id, blob, policy.retention_days)
        metadata = {
            "public_specification": public_config(raw, redact_content_hashes=True),
            "plan_hash": digest(raw),
            "artifact_ref": ref,
            "policy_id": str(parent.policy_id),
        }
    refs.append(metadata["artifact_ref"])
    metadata["expires_at"] = parent.expires_at.isoformat()
    metadata["campaign_id"] = str(parent.id)
    metadata["campaign_sha256"] = parent.approval_sha256
    row = ExperimentPlanRecord(
        project_id=parent.project_id,
        dataset_id=parent.dataset_id,
        specification=metadata,
        approval_status="consumed",
        approved_by=parent.approved_by,
    )
    session.add(row)
    session.flush()
    return row


def child_run(session, store, parent, plan, phase, policy, refs, *, derivation=None):
    step = f"{phase}-r{parent.current_round}"
    if session.scalar(
        select(CampaignRun.id).where(
            CampaignRun.campaign_id == parent.id, CampaignRun.step_key == step
        )
    ):
        raise ValueError("CAMPAIGN_STEP_ALREADY_FROZEN")
    record = freeze_raw(session, store, parent, plan, policy, refs)
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
    if phase != "proposal":
        from cutback.server.stages import validate_stage

        validate_stage(session, store, parent.project_id, plan, run_id=run.id)
        run.results = {
            "lineage": reserve_evaluation(
                session, parent.project_id, parent.dataset_id, plan, run.id
            )
        }
    session.add(
        JobRecord(
            project_id=parent.project_id,
            run_id=run.id,
            status="QUEUED",
            execution_kind="proposal" if phase == "proposal" else "evaluation",
        )
    )
    session.add(
        CampaignRun(
            project_id=parent.project_id,
            environment_id=parent.environment_id,
            campaign_id=parent.id,
            run_id=run.id,
            phase=phase,
            step_key=step,
            round_index=parent.current_round,
            campaign_sha256=parent.approval_sha256,
            plan_sha256=record.specification["plan_hash"],
            derivation=derivation or {},
        )
    )
    parent.status = "WAITING_" + phase.upper()
    return run


def freeze_candidate(session, store, parent, model, kind, policy, refs, ordinal, proposal_run=None):
    if (
        session.scalar(
            select(func.count())
            .select_from(CampaignCandidate)
            .where(CampaignCandidate.project_id == parent.project_id)
        )
        >= 10000
    ):
        raise ValueError("CAMPAIGN_PROJECT_CANDIDATE_QUOTA_EXHAUSTED")
    payload = put_payload(
        session, store, parent, model.model_dump(mode="json"), "campaign_candidate", refs, policy
    )
    row = CampaignCandidate(
        project_id=parent.project_id,
        environment_id=parent.environment_id,
        campaign_id=parent.id,
        proposal_run_id=proposal_run,
        payload_id=payload.id,
        kind=kind,
        model_key=model.key,
        round_index=0 if kind == "seed" else parent.current_round,
        ordinal=ordinal,
        configuration_sha256=payload.content_hash,
        configuration=public_config(model.model_dump(mode="json"), redact_content_hashes=True),
    )
    session.add(row)
    session.flush()
    return row


def target_plan(parent, campaign, dataset, candidates, *, confirmation_run=None, nonce=None):
    raw = campaign.template.model_dump(mode="json")
    raw.update(
        candidates=[c.model_dump(mode="json") for c in candidates],
        name=f"campaign {str(parent.id)[:8]} round {parent.current_round}",
        split="test" if confirmation_run else "validation",
        predecessor_run_id=str(confirmation_run) if confirmation_run else None,
        content_nonce=str(nonce or uuid4()),
        budget={
            "max_usd": str(parent.economic_envelope["effective_max_usd"]),
            "max_requests": parent.economic_envelope["max_requests"],
            "max_wall_seconds": remaining_wall(parent),
        },
    )
    plan = ExperimentPlan.model_validate(raw)
    summary = plan_summary(plan, dataset)
    if not summary["fits_budget"]:
        raise ValueError("CAMPAIGN_DERIVED_PLAN_EXCEEDS_FROZEN_ENVELOPE")
    raw["budget"].update(
        max_usd=summary["worst_case_modeled_usd"], max_requests=summary["requests"]
    )
    return ExperimentPlan.model_validate(raw)


def prior_feedback(session, parent):
    runs = session.scalars(
        select(ExperimentRunRecord)
        .join(CampaignRun, CampaignRun.run_id == ExperimentRunRecord.id)
        .where(CampaignRun.campaign_id == parent.id, CampaignRun.phase == "validation")
        .order_by(CampaignRun.round_index)
        .limit(3)
    ).all()
    feedback = []
    for run in runs:
        for candidate in run.results.get("search_frontier", {}).get("candidates", []):
            if candidate.get("baseline"):
                continue
            feedback.append(
                GenerationFeedback(
                    candidate_key=candidate["key"],
                    quality_gate_passed=candidate["eligible"],
                    sample_metric_min=candidate["sample_quality_min"],
                    cost_complete=candidate["cost_per_request_usd"] is not None,
                    sample_cost_per_request_usd=candidate["cost_per_request_usd"],
                )
            )
    return feedback


def queue_proposal(session, store, parent, campaign, policy, refs, dataset):
    from cutback.server.lineage_db import DatasetExposure

    training_lineage(session, parent.project_id, dataset)
    previous = session.scalars(
        select(CampaignCandidate)
        .where(
            CampaignCandidate.campaign_id == parent.id,
            CampaignCandidate.kind == "generated",
            CampaignCandidate.round_index < parent.current_round,
        )
        .order_by(CampaignCandidate.round_index, CampaignCandidate.ordinal)
        .limit(9)
    ).all()
    if len(previous) > 8:
        raise ValueError("GENERATION_PRIOR_VERSION_BOUND")
    variants = [candidate_model(session, store, parent, row, policy) for row in previous]
    text, families = generation_input(
        campaign, dataset["items"], prior_feedback(session, parent), prior_variants=variants
    )
    exposed = session.scalar(
        select(func.count())
        .select_from(DatasetExposure)
        .where(DatasetExposure.project_id == parent.project_id)
    )
    training_groups = {
        str(i["canonical_family_id"]) for i in dataset["items"] if i["split"] == "train"
    }
    if exposed + len(training_groups) > 100000:
        raise ValueError("CAMPAIGN_PROJECT_LINEAGE_RECEIPT_QUOTA_EXHAUSTED")
    # Conservatively reserve every training family from this dataset. This is a
    # superset of the selected generator examples, never a fresh-test assertion.
    expose(
        session,
        parent.project_id,
        parent.dataset_id,
        "train",
        f"campaign:{parent.id}:r{parent.current_round}",
    )
    payload = put_payload(
        session, store, parent, {"input": text}, "campaign_generator_input", refs, policy
    )
    model = proposal_model(campaign.generator)
    plan = ProposalOnlyPlan(
        campaign_id=parent.id,
        campaign_sha256=parent.approval_sha256,
        round_index=parent.current_round,
        workflow=campaign.template.workflow,
        dataset_hash=campaign.template.dataset_hash,
        input_payload_id=payload.id,
        input_sha256=payload.content_hash,
        baseline=model,
        budget=Budget(
            max_usd=upper_cost(model), max_requests=1, max_wall_seconds=remaining_wall(parent)
        ),
    )
    child_run(
        session,
        store,
        parent,
        plan,
        "proposal",
        policy,
        refs,
        derivation={
            "generator_version": campaign.generator.version,
            "training_families_sent": len(families),
            "training_exposure_scope": "all_training_groups_conservative_superset",
            "input_sha256": payload.content_hash,
        },
    )


def fail_parent(session, parent, code):
    parent.status, parent.completed_at = "FAILED", datetime.now(UTC)
    parent.summary = {"code": code, "deployment_authorized": False}
    children = session.scalars(
        select(ExperimentRunRecord)
        .join(CampaignRun, CampaignRun.run_id == ExperimentRunRecord.id)
        .where(CampaignRun.campaign_id == parent.id)
        .with_for_update(of=ExperimentRunRecord)
    ).all()
    for child in children:
        if child.status in {"QUEUED", "BLOCKED", "RUNNING"}:
            child.status = "CANCELLED"
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == child.id).with_for_update()
            )
            if job:
                job.status = "CANCELLED"


def select_confirmation(session, store, parent, policy, refs, dataset):
    project = parent.project_id
    choices = []
    runs = session.scalars(
        select(ExperimentRunRecord)
        .join(CampaignRun, CampaignRun.run_id == ExperimentRunRecord.id)
        .where(CampaignRun.campaign_id == parent.id, CampaignRun.phase == "validation")
        .limit(3)
    ).all()
    for run in runs:
        for item in run.results.get("search_frontier", {}).get("candidates", []):
            if item.get("eligible") and item.get("cost_per_request_usd") is not None:
                choices.append(
                    (
                        Decimal(item["cost_per_request_usd"]),
                        item["key"],
                        str(run.id),
                    )
                )
    if not choices:
        parent.status, parent.completed_at = "COMPLETED", datetime.now(UTC)
        parent.summary = {
            **parent.summary,
            "code": "search_completed_no_eligible_candidate",
            "decision": "reject_no_quality_preserving_lower_cost_candidate",
            "deployment_authorized": False,
        }
    else:
        _, key, selected_run = min(choices)
        candidate_row = session.scalar(
            select(CampaignCandidate).where(
                CampaignCandidate.campaign_id == parent.id,
                CampaignCandidate.model_key == key,
            )
        )
        if candidate_row is None:
            raise ValueError("CAMPAIGN_SELECTED_VERSION_NOT_FROZEN")
        selected = candidate_model(session, store, parent, candidate_row, policy)
        prior = session.get(ExperimentRunRecord, UUID(selected_run))
        prior_row = session.get(ExperimentPlanRecord, prior.plan_id)
        prior_plan = load_plan(store, project, prior_row.specification)
        # Keep the exact prior contract/name/nonce; only ordinary
        # confirmation fields may differ under validate_confirmation.
        raw_plan = prior_plan.model_dump(mode="json")
        raw_plan.update(
            candidates=[selected.model_dump(mode="json")],
            split="test",
            predecessor_run_id=selected_run,
            budget={
                "max_usd": parent.economic_envelope["effective_max_usd"],
                "max_requests": parent.economic_envelope["max_requests"],
                "max_wall_seconds": remaining_wall(parent),
            },
        )
        confirmation = ExperimentPlan.model_validate(raw_plan)
        summary = plan_summary(confirmation, dataset)
        if not summary["fits_budget"]:
            raise ValueError("CAMPAIGN_CONFIRMATION_EXCEEDS_FROZEN_ENVELOPE")
        raw_plan["budget"].update(
            max_requests=summary["requests"], max_usd=summary["worst_case_modeled_usd"]
        )
        confirmation = ExperimentPlan.model_validate(raw_plan)
        session.add(
            CampaignSelection(
                campaign_id=parent.id,
                project_id=project,
                environment_id=parent.environment_id,
                candidate_id=candidate_row.id,
                validation_run_id=prior.id,
                selection_sha256=digest(
                    {
                        "configuration": candidate_row.configuration_sha256,
                        "validation_plan": prior_row.specification["plan_hash"],
                    }
                ),
            )
        )
        child_run(
            session,
            store,
            parent,
            confirmation,
            "confirmation",
            policy,
            refs,
            derivation={
                "selected_candidate_id": str(candidate_row.id),
                "validation_run_id": selected_run,
            },
        )


def advance_one(
    sessions, store, *, execution_enabled=False, only_campaign_id=None, synthetic_transport=False
):
    """One idempotent metadata transition, no SDK or application execution."""
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    now = datetime.now(UTC)
    with sessions() as reader:
        query = select(CampaignRecord.id, CampaignRecord.project_id).where(
            CampaignRecord.status.in_(["QUEUED", "RUNNING", *WAITING]),
            or_(CampaignRecord.available_at.is_(None), CampaignRecord.available_at <= now),
            or_(CampaignRecord.lease_until.is_(None), CampaignRecord.lease_until <= now),
        )
        if only_campaign_id:
            query = query.where(CampaignRecord.id == UUID(str(only_campaign_id)))
        candidate = reader.execute(
            query.order_by(
                CampaignRecord.available_at.asc().nullsfirst(),
                CampaignRecord.created_at,
                CampaignRecord.id,
            ).limit(1)
        ).first()
    if candidate is None:
        return {"status": "IDLE"}
    parent_id, project = candidate
    try:
        with content_transaction(sessions, store, project) as (session, refs):
            lock_lineage(session, project)
            parent = session.get(CampaignRecord, parent_id, with_for_update=True)
            if parent.status not in {"QUEUED", "RUNNING", *WAITING} or (
                parent.available_at and parent.available_at > datetime.now(UTC)
            ):
                return {"status": "IDLE"}
            parent.available_at = datetime.now(UTC) + timedelta(seconds=1)
            parent.lease_token, parent.lease_until = (
                uuid4(),
                datetime.now(UTC) + timedelta(seconds=30),
            )
            if parent.status == "QUEUED":
                parent.status = "RUNNING"
            campaign, policy, row, _ = authorize_campaign(
                session, store, parent, synthetic_transport=synthetic_transport
            )
            dataset, _ = load_dataset(session, row, store)
            if parent.status == "RUNNING":
                for ordinal, seed in enumerate(campaign.template.candidates, 1):
                    freeze_candidate(session, store, parent, seed, "seed", policy, refs, ordinal)
                queue_proposal(session, store, parent, campaign, policy, refs, dataset)
            else:
                phase = parent.status.removeprefix("WAITING_").lower()
                link = session.scalar(
                    select(CampaignRun).where(
                        CampaignRun.campaign_id == parent.id,
                        CampaignRun.step_key == f"{phase}-r{parent.current_round}",
                    )
                )
                if link is None:
                    raise ValueError("CAMPAIGN_ACTIVE_CHILD_MISSING")
                child = session.get(ExperimentRunRecord, link.run_id)
                if child.status in {"QUEUED", "BLOCKED", "RUNNING"}:
                    parent.lease_token = parent.lease_until = None
                    return {
                        "status": parent.status,
                        "campaign_id": str(parent.id),
                        "provider_calls": 0,
                    }
                if (
                    child.status != "COMPLETED"
                    or child.known_cost_requests != child.requests_used
                    or child.call_ledger_version != 1
                    or not child.requests_used
                    or child.results.get("billing_complete") is not True
                ):
                    raise ValueError("CAMPAIGN_CHILD_FAILED_OR_BILLING_INCOMPLETE_NO_REPLAY")
                child_plan = session.get(ExperimentPlanRecord, child.plan_id)
                if (
                    link.campaign_sha256 != parent.approval_sha256
                    or child_plan.specification["plan_hash"] != link.plan_sha256
                    or phase != "proposal"
                    and child.results.get("plan", {}).get("plan_hash") != link.plan_sha256
                ):
                    raise ValueError("CAMPAIGN_CHILD_FROZEN_RECEIPT_CHANGED")
                if phase == "proposal":
                    raw, sha = read_payload(
                        session, store, parent, UUID(child.results["output_payload_id"]), policy
                    )
                    if sha != child.results["output_sha256"]:
                        raise ValueError("CAMPAIGN_PROPOSAL_OUTPUT_CHANGED")
                    proposed = decode_proposals(
                        raw["output"], campaign, round_index=parent.current_round
                    )
                    previous = session.scalars(
                        select(CampaignCandidate)
                        .where(
                            CampaignCandidate.campaign_id == parent.id,
                            CampaignCandidate.round_index < parent.current_round,
                        )
                        .limit(14)
                    ).all()
                    if len(previous) > 13:
                        raise ValueError("CAMPAIGN_FROZEN_VERSION_BOUND")
                    known = {
                        digest(
                            candidate_model(session, store, parent, c, policy).model_dump(
                                mode="json", exclude={"key"}
                            )
                        ): str(c.id)
                        for c in previous
                    }
                    fresh, dispositions = [], []
                    for ordinal, model in enumerate(proposed, 1):
                        version = freeze_candidate(
                            session,
                            store,
                            parent,
                            model,
                            "generated",
                            policy,
                            refs,
                            ordinal,
                            child.id,
                        )
                        identity = digest(model.model_dump(mode="json", exclude={"key"}))
                        duplicate = known.get(identity)
                        dispositions.append(
                            {
                                "candidate_id": str(version.id),
                                "status": "duplicate_not_evaluated"
                                if duplicate
                                else "new_configuration",
                                "duplicate_of": duplicate,
                            }
                        )
                        if not duplicate:
                            fresh.append(model)
                            known[identity] = str(version.id)
                    link.derivation = {**link.derivation, "candidate_dispositions": dispositions}
                    if fresh:
                        plan = target_plan(
                            parent, campaign, dataset, [*campaign.template.candidates, *fresh]
                        )
                        child_run(
                            session,
                            store,
                            parent,
                            plan,
                            "validation",
                            policy,
                            refs,
                            derivation={
                                "proposal_run_id": str(child.id),
                                "permitted_changes": ["instruction", "max_output_tokens"],
                            },
                        )
                    else:
                        parent.summary = {
                            "stopping_reason": "duplicate_proposals",
                            "deployment_authorized": False,
                        }
                        select_confirmation(session, store, parent, policy, refs, dataset)
                elif phase == "validation" and parent.current_round < campaign.generator.rounds:
                    parent.current_round += 1
                    queue_proposal(session, store, parent, campaign, policy, refs, dataset)
                elif phase == "validation":
                    select_confirmation(session, store, parent, policy, refs, dataset)
                elif phase == "confirmation":
                    from cutback.evals.search import regression_gate

                    gate = regression_gate(child.results, expected_plan_hash=link.plan_sha256)
                    sample_gate = regression_gate(
                        child.results, expected_plan_hash=link.plan_sha256, sample_only=True
                    )
                    parent.status, parent.completed_at = "COMPLETED", datetime.now(UTC)
                    decision = "confirmation_requires_review_or_rejected"
                    if gate["passed"]:
                        decision = "prepare_application_checks"
                    elif (
                        row.provenance["source_type"] == "synthetic_fixture"
                        and sample_gate["passed"]
                    ):
                        decision = "synthetic_confirmation_passed_real_data_required"
                    parent.summary = {
                        **parent.summary,
                        "code": "confirmation_completed",
                        "decision": decision,
                        "confirmation_gate": gate,
                        "sample_only_gate": sample_gate,
                        "optimization_cost_complete": parent.requests_used
                        == parent.known_cost_requests,
                        "production_savings_usd": None,
                        "deployment_authorized": False,
                    }
            parent.lease_token = parent.lease_until = None
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="campaign_worker",
                    action="campaign_advanced",
                    details={
                        "campaign_id": str(parent.id),
                        "status": parent.status,
                        "round": parent.current_round,
                    },
                )
            )
            result = {"status": parent.status, "campaign_id": str(parent.id), "provider_calls": 0}
        return result
    except Exception as exc:
        import re

        code = str(exc) if type(exc) is ValueError else ""
        if not re.fullmatch(r"[A-Z_]{3,100}", code):
            code = "CAMPAIGN_AUTHORITY_INPUT_OR_TRANSITION_FAILED_NO_REPLAY"
        with sessions.begin() as session:
            lock_lineage(session, project)
            parent = session.get(CampaignRecord, parent_id, with_for_update=True)
            if parent and parent.status in {"QUEUED", "RUNNING", *WAITING}:
                fail_parent(session, parent, code)
                parent.lease_token = parent.lease_until = None
        return {"status": "FAILED", "campaign_id": str(parent_id), "provider_calls": 0}
