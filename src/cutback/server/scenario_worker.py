"""One native scenario-generation request. Uncertain dispatch is never replayed."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, true

from cutback.core.observation import observation_scope
from cutback.evals.datasets import Case
from cutback.evals.experiments import BudgetLedger
from cutback.evals.scenario_generation import decode, materialize
from cutback.server.campaign_content import content_transaction
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, ExperimentPlanRecord, ExperimentRunRecord, JobRecord
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.scenario_content import put_payload
from cutback.server.scenario_db import ScenarioVersionRecord
from cutback.server.scenario_service import generation_authority, load_generation, version_sha256


def recover_scenario_states(sessions, *, only_run_id=None):
    with sessions() as session:
        rows = session.execute(
            select(ScenarioVersionRecord.id, ScenarioVersionRecord.project_id)
            .join(
                ExperimentRunRecord,
                ScenarioVersionRecord.generation_run_id == ExperimentRunRecord.id,
            )
            .where(
                ScenarioVersionRecord.state.in_(["QUEUED", "RUNNING"]),
                ExperimentRunRecord.status.in_(["FAILED", "CANCELLED"]),
                ExperimentRunRecord.id == only_run_id if only_run_id else true(),
            )
            .limit(100)
        ).all()
    for identifier, project in rows:
        with sessions.begin() as session:
            lock_lineage(session, project)
            row = session.get(ScenarioVersionRecord, identifier, with_for_update=True)
            if not row:
                continue
            run = session.get(ExperimentRunRecord, row.generation_run_id)
            if row.state in {"QUEUED", "RUNNING"} and run.status in {"FAILED", "CANCELLED"}:
                row.state = run.status


def run_scenario_one(sessions, store, *, executor=None, execution_enabled=False, only_run_id=None):
    if not execution_enabled:
        return {"status": "EXPERIMENT_EXECUTION_DISABLED", "request_reservations": 0}
    from cutback.server.execution_ledger import recover_execution_leases

    only_run_id = UUID(str(only_run_id)) if only_run_id else None
    recover_execution_leases(sessions, only_run_id=only_run_id)
    recover_scenario_states(sessions, only_run_id=only_run_id)
    synthetic = getattr(executor, "synthetic_transport", False) is True
    with sessions() as session:
        candidate = session.execute(
            select(JobRecord.id, JobRecord.project_id)
            .where(
                JobRecord.execution_kind == "scenario_generation",
                JobRecord.status.in_(
                    ["QUEUED", "BLOCKED"] if synthetic and only_run_id else ["QUEUED"]
                ),
                JobRecord.run_id == only_run_id if only_run_id else true(),
            )
            .order_by(JobRecord.created_at, JobRecord.id)
            .limit(1)
        ).first()
    if not candidate:
        return {"status": "IDLE"}
    job_id, project = candidate
    owned = executor is None
    run_id = None
    claimed = False
    operation: str | None = None
    try:
        # Read the approved transport before constructing an optional provider client.
        with sessions.begin() as session:
            lock_lineage(session, project)
            job = session.get(JobRecord, job_id, with_for_update=True)
            if not job:
                return {"status": "UNAVAILABLE"}
            if job.status not in (
                {"QUEUED", "BLOCKED"} if synthetic and only_run_id else {"QUEUED"}
            ):
                return {"status": "IDLE"}
            run = session.get(ExperimentRunRecord, job.run_id, with_for_update=True)
            run_id = run.id
            row = session.scalar(
                select(ScenarioVersionRecord)
                .where(
                    ScenarioVersionRecord.project_id == project,
                    ScenarioVersionRecord.generation_run_id == run.id,
                )
                .with_for_update()
            )
            if not row or row.state != "QUEUED" or run.status not in {"QUEUED", "BLOCKED"}:
                raise ValueError("SCENARIO_QUEUED_PARENT_AND_RUN_REQUIRED")
            plan_row = session.get(ExperimentPlanRecord, run.plan_id)
            plan = load_generation(store, project, plan_row.specification)
            operation = plan.operation
            if plan.transport == "scripted_test_only" and not synthetic:
                job.status = "BLOCKED"
                job.available_at = None
                run.status = "BLOCKED"
                run.results = {
                    "operation": operation,
                    "code": "SCRIPTED_SCENARIO_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR",
                    "requests": 0,
                    "billing_complete": True,
                }
                return {"status": "BLOCKED", "run_id": str(run.id), "request_reservations": 0}
        if owned:
            try:
                from cutback.evals.executors import OpenAIExecutor

                executor = OpenAIExecutor()
            except (ValueError, ImportError):
                return {
                    "status": "NO_SCENARIO_GENERATOR_KEY_OR_SUPPORTED_EXTRA",
                    "request_reservations": 0,
                }
        with sessions.begin() as session:
            lock_lineage(session, project)
            job = session.get(JobRecord, job_id, with_for_update=True)
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            row = session.scalar(
                select(ScenarioVersionRecord)
                .where(
                    ScenarioVersionRecord.project_id == project,
                    ScenarioVersionRecord.generation_run_id == run_id,
                )
                .with_for_update()
            )
            if not job or not run or not row:
                return {"status": "UNAVAILABLE", "run_id": str(run_id)}
            if (
                job.status not in {"QUEUED", "BLOCKED"}
                or run.status not in {"QUEUED", "BLOCKED"}
                or row.state != "QUEUED"
            ):
                return {"status": run.status, "run_id": str(run.id)}
            plan, policy, value, requirements = generation_authority(
                session, store, row, synthetic_transport=synthetic
            )
            row.state = run.status = job.status = "RUNNING"
            job.attempts += 1
            job.available_at = None
            job.lease_until = datetime.now(UTC) + timedelta(
                seconds=plan.budget.max_wall_seconds + 60
            )
            identifier, environment = row.id, row.environment_id
            text = value["input"]
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="scenario_worker",
                    action="scenario_generation_started",
                    details={"scenario_id": str(row.id), "run_id": str(run.id)},
                )
            )
            claimed = True

        def authority(session):
            current = session.get(ScenarioVersionRecord, identifier, populate_existing=True)
            current_plan, _, current_value, _ = generation_authority(
                session, store, current, synthetic_transport=synthetic
            )
            if current_plan != plan or current_value != value:
                raise ValueError("SCENARIO_GENERATOR_APPROVED_INPUT_CHANGED")

        ledger = ExecutionLedger(sessions, run_id, job_id, plan, environment, authorize=authority)
        local = BudgetLedger(
            plan.budget,
            price_sha256=plan.price_sha256,
            reserve_call=ledger.reserve,
            record_call=ledger.record,
        )
        with observation_scope("optimization", run_id=run_id):
            result = local.call(executor, plan.baseline, text, traffic_kind="optimization")
        proposal = None
        valid = result.complete and local.billing_complete
        if valid:
            try:
                proposal = decode(result.output, requirements, value["training"])
                materialize(
                    proposal,
                    requirements,
                    {key: Case.model_validate(case) for key, case in value["training"].items()},
                )
            except ValueError:
                valid = False
        with content_transaction(sessions, store, project) as (session, refs):
            lock_lineage(session, project)
            row = session.get(ScenarioVersionRecord, identifier, with_for_update=True)
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            job = session.get(JobRecord, job_id, with_for_update=True)
            authority(session)
            if (
                row.state != "RUNNING"
                or run.status != "RUNNING"
                or job.status != "RUNNING"
                or job.lease_until <= datetime.now(UTC)
            ):
                raise ValueError("SCENARIO_CANCELLED_OR_LEASE_EXPIRED_AFTER_CALL")
            payload = put_payload(
                session, store, row, {"output": result.output}, "scenario_output", refs, policy
            )
            row.output_payload_id = payload.id
            row.state = run.status = job.status = "COMPLETED" if valid else "FAILED"
            row.version_sha256 = (
                version_sha256(row, plan.input_sha256, payload.content_hash) if valid else None
            )
            run.results = {
                "operation": operation,
                "status": run.status,
                "code": "SCENARIO_PROPOSAL_REQUIRES_OWNER_REVIEW"
                if valid
                else "SCENARIO_GENERATOR_RESPONSE_INVALID_NO_REPLAY",
                "scenario_id": str(row.id),
                "scenario_version_sha256": row.version_sha256,
                "proposed_scenarios_count": len(proposal.scenarios) if proposal else None,
                "requests": run.requests_used,
                "known_cost_requests": run.known_cost_requests,
                "billing_complete": run.requests_used == run.known_cost_requests,
                "known_spend_subtotal_usd": str(run.spent_usd),
                "quality_evidence": "none_proposal_only",
                "owner_review_required": True,
            }
            session.add(
                AuditEvent(
                    project_id=project,
                    actor="scenario_worker",
                    action="scenario_generation_completed",
                    details={"scenario_id": str(row.id), "run_id": str(run.id), "valid": valid},
                )
            )
            return {"status": run.status, "run_id": str(run.id), "scenario_id": str(row.id)}
    except Exception:
        if run_id:
            with sessions.begin() as session:
                lock_lineage(session, project)
                row = session.scalar(
                    select(ScenarioVersionRecord)
                    .where(
                        ScenarioVersionRecord.project_id == project,
                        ScenarioVersionRecord.generation_run_id == run_id,
                    )
                    .with_for_update()
                )
                run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
                job = session.get(JobRecord, job_id, with_for_update=True)
                if not run or not job:
                    return {"status": "UNAVAILABLE", "run_id": str(run_id)}
                if row is None and run.status in {"QUEUED", "BLOCKED", "RUNNING"}:
                    run.status = job.status = "FAILED"
                    run.results = {
                        "operation": operation,
                        "code": "SCENARIO_PARENT_UNAVAILABLE_NO_REPLAY",
                    }
                if (
                    row
                    and row.state in {"QUEUED", "RUNNING"}
                    and run.status in {"QUEUED", "BLOCKED", "RUNNING"}
                ):
                    row.state = run.status = job.status = "FAILED"
                    run.results = {
                        "operation": operation,
                        "status": "FAILED",
                        "code": "SCENARIO_AUTHORITY_NATIVE_OR_STORAGE_FAILED_NO_REPLAY",
                        "requests": run.requests_used,
                        "known_cost_requests": run.known_cost_requests,
                        "billing_complete": run.requests_used == run.known_cost_requests,
                        "known_spend_subtotal_usd": str(run.spent_usd),
                        "claimed": claimed,
                    }
                status = run.status
            return {"status": status, "run_id": str(run_id)}
        return {"status": "FAILED", "request_reservations": 0}
    finally:
        if owned and executor is not None:
            executor.close()
