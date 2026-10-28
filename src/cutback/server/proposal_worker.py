"""Qualified native proposer, one durable physical call and no automatic replay."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select

from cutback.core.observation import observation_scope
from cutback.evals.campaigns import ProposalOnlyPlan, decode_proposals
from cutback.evals.datasets import digest
from cutback.evals.experiments import BudgetLedger
from cutback.server.campaign_content import content_transaction, put_payload, read_payload
from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.campaign_service import authorize_campaign
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_ledger import ExecutionLedger, recover_execution_leases


def load_proposal(store, project, metadata):
    if datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(UTC):
        raise ValueError("PROPOSAL_PLAN_EXPIRED")
    try:
        raw = json.loads(store.get(project, metadata["artifact_ref"]))
        plan = ProposalOnlyPlan.model_validate(raw)
    except Exception:
        raise ValueError("PROPOSAL_PLAN_UNAVAILABLE_OR_INVALID") from None
    if (
        digest(raw) != metadata["plan_hash"]
        or digest(plan.model_dump(mode="json")) != metadata["plan_hash"]
    ):
        raise ValueError("PROPOSAL_FROZEN_PLAN_CHANGED")
    return plan


def run_proposal_one(sessions, store, *, executor=None, execution_enabled=False, only_run_id=None):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "provider_calls": 0}
    only_run_id = UUID(str(only_run_id)) if only_run_id else None
    now = datetime.now(UTC)
    recover_execution_leases(sessions, only_run_id=only_run_id, now=now)
    with sessions() as reader:
        expired = reader.execute(
            select(JobRecord.id, JobRecord.project_id)
            .where(
                JobRecord.execution_kind == "proposal",
                JobRecord.status == "RUNNING",
                JobRecord.lease_until <= now,
            )
            .limit(100)
        ).all()
    for job_id, project in expired:
        with sessions.begin() as session:
            lock_lineage(session, project)
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job.status == "RUNNING" and job.lease_until <= now:
                job.status = "FAILED"
                run = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
                if run.status == "RUNNING":
                    run.status, run.results = "FAILED", {"code": "PROPOSER_LEASE_EXPIRED_NO_REPLAY"}
    with sessions() as reader:
        query = select(JobRecord.id, JobRecord.project_id).where(
            JobRecord.execution_kind == "proposal", JobRecord.status == "QUEUED"
        )
        if only_run_id:
            query = query.where(JobRecord.run_id == only_run_id)
        found = reader.execute(query.order_by(JobRecord.created_at, JobRecord.id).limit(1)).first()
    if not found:
        return {"status": "IDLE"}
    owned = executor is None
    if owned:
        try:
            from cutback.evals.executors import OpenAIExecutor

            executor = OpenAIExecutor()
        except (ValueError, ImportError):
            return {"status": "NO_PROPOSER_API_KEY_OR_SUPPORTED_EXTRA", "provider_calls": 0}
    job_id, project = found
    run_id = None
    try:
        with sessions.begin() as session:
            lock_lineage(session, project)
            job = session.get(JobRecord, job_id, with_for_update=True)
            if job.status != "QUEUED":
                return {"status": "IDLE"}
            run = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
            run_id = run.id
            if run.status != "QUEUED":
                job.status = run.status
                return {"status": run.status, "provider_calls": 0}
            link = session.scalar(select(CampaignRun).where(CampaignRun.run_id == run.id))
            parent = session.get(CampaignRecord, link.campaign_id) if link else None
            if not parent or link.phase != "proposal":
                raise ValueError("PROPOSER_REQUIRES_FROZEN_CAMPAIGN_CHILD")
            campaign, policy, _, _ = authorize_campaign(
                session,
                store,
                parent,
                synthetic_transport=getattr(executor, "synthetic_transport", False),
            )
            plan_row = session.get(ExperimentPlanRecord, run.plan_id)
            plan = load_proposal(store, project, plan_row.specification)
            if (
                plan.campaign_id != parent.id
                or plan.campaign_sha256 != parent.approval_sha256
                or link.plan_sha256 != plan_row.specification["plan_hash"]
                or plan.round_index != parent.current_round
            ):
                raise ValueError("PROPOSER_PARENT_OR_DERIVATION_CHANGED")
            payload, sha = read_payload(session, store, parent, plan.input_payload_id, policy)
            if (
                sha != plan.input_sha256
                or set(payload) != {"input"}
                or not isinstance(payload["input"], str)
            ):
                raise ValueError("PROPOSER_FROZEN_INPUT_CHANGED")
            text = payload["input"]
            run.status = job.status = "RUNNING"
            job.attempts += 1
            job.lease_until = datetime.now(UTC) + timedelta(
                seconds=plan.budget.max_wall_seconds + 60
            )
            parent_id, environment_id = parent.id, parent.environment_id

        def authority(session):
            active = session.get(CampaignRecord, parent_id, populate_existing=True)
            _, active_policy, _, _ = authorize_campaign(
                session,
                store,
                active,
                synthetic_transport=getattr(executor, "synthetic_transport", False),
            )
            value, sha = read_payload(session, store, active, plan.input_payload_id, active_policy)
            if sha != plan.input_sha256 or value != {"input": text}:
                raise ValueError("PROPOSER_FROZEN_INPUT_CHANGED")

        ledger = ExecutionLedger(
            sessions, run_id, job_id, plan, environment_id, authorize=authority
        )
        local = BudgetLedger(
            plan.budget,
            price_sha256=plan.price_sha256,
            reserve_call=ledger.reserve,
            record_call=ledger.record,
        )
        with observation_scope("optimization", run_id=run_id):
            result = local.call(executor, plan.baseline, text, traffic_kind="optimization")
        # Decode before storing/accepting versions. Even invalid output remains
        # encrypted for an authorized review; it never becomes an evaluation plan.
        valid = result.complete and local.billing_complete
        if valid:
            try:
                decode_proposals(result.output, campaign, round_index=plan.round_index)
            except ValueError:
                valid = False
        with content_transaction(sessions, store, project) as (session, refs):
            lock_lineage(session, project)
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id, with_for_update=True)
            parent = session.get(CampaignRecord, parent_id)
            authority(session)
            if (
                run.status != "RUNNING"
                or job.status != "RUNNING"
                or job.lease_until <= datetime.now(UTC)
            ):
                return {"status": run.status, "run_id": str(run_id)}
            payload = put_payload(
                session,
                store,
                parent,
                {"output": result.output},
                "campaign_generator_output",
                refs,
                policy,
            )
            run.status = job.status = "COMPLETED" if valid else "FAILED"
            run.results = {
                "operation": "candidate-generation-v1",
                "status": run.status,
                "code": "PROPOSALS_FROZEN_FOR_VALIDATION"
                if valid
                else "GENERATOR_RESPONSE_INVALID_NO_REPLAY",
                "output_payload_id": str(payload.id),
                "output_sha256": payload.content_hash,
                "requests": run.requests_used,
                "known_cost_requests": run.known_cost_requests,
                "billing_complete": run.requests_used == run.known_cost_requests,
                "known_spend_subtotal_usd": str(run.spent_usd),
                "quality_evidence": "none_proposal_only",
            }
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="proposal_worker",
                    action="campaign_proposals_recorded",
                    details={"campaign_id": str(parent.id), "run_id": str(run.id), "valid": valid},
                )
            )
            status = run.status
        return {"status": status, "run_id": str(run_id)}
    except Exception:
        if run_id:
            with sessions.begin() as session:
                lock_lineage(session, project)
                run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
                job = session.get(JobRecord, job_id, with_for_update=True)
                if run.status in {"QUEUED", "RUNNING"}:
                    run.status = job.status = "FAILED"
                    run.results = {
                        "code": "PROPOSER_AUTHORITY_EXECUTION_OR_STORAGE_FAILED_NO_REPLAY",
                        "requests": run.requests_used,
                        "known_cost_requests": run.known_cost_requests,
                        "billing_complete": run.requests_used == run.known_cost_requests,
                    }
        return {"status": "FAILED", "run_id": str(run_id) if run_id else None}
    finally:
        if owned:
            executor.close()
