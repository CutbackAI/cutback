"""Atomic experiment budget/call reservations. No content or SDK requests in this module."""

import hashlib
import hmac
import math
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid4

from sqlalchemy import func, select, true

from cutback.core.input_routing import RoutingTrainingExecution
from cutback.evals.datasets import digest
from cutback.evals.experiments import provider_models, upper_cost
from cutback.pricing import Catalog
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import (
    AuditEvent,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
)
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.principals import principal_role

MAX_EXECUTION_CALLS = 100000
PURPOSES = {"evaluation", "judge", "optimization", "simulation"}


def recover_execution_leases(sessions, *, only_run_id=None, now=None):
    now = now or datetime.now(UTC)
    with sessions() as session:
        calls = select(ExecutionCall.run_id, ExecutionCall.project_id).where(
            ExecutionCall.state == "reserved",
            ExecutionCall.lease_until <= now,
            ExecutionCall.run_id == only_run_id if only_run_id else true(),
        )
        episodes = (
            select(ExecutionEpisode.run_id, ExecutionEpisode.project_id)
            .join(JobRecord, ExecutionEpisode.run_id == JobRecord.run_id)
            .where(
                ExecutionEpisode.state == "running",
                JobRecord.lease_until <= now,
                ExecutionEpisode.run_id == only_run_id if only_run_id else true(),
            )
        )
        jobs = select(JobRecord.run_id, JobRecord.project_id).where(
            JobRecord.status == "RUNNING",
            JobRecord.lease_until <= now,
            JobRecord.run_id == only_run_id if only_run_id else true(),
        )
        runs = session.execute(calls.union(episodes, jobs).limit(100)).all()
    for run_id, project_id in runs:
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            run = session.get(ExperimentRunRecord, run_id, with_for_update=True)
            if not run:
                continue
            job = session.scalar(
                select(JobRecord).where(JobRecord.run_id == run_id).with_for_update()
            )
            if job and job.status == "RUNNING" and job.lease_until and job.lease_until <= now:
                if run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                    run.status = "FAILED"
                    run.results = {"code": "WORKER_LEASE_EXPIRED_BILLING_UNCERTAIN"}
                job.status = run.status
                session.add(
                    AuditEvent(
                        project_id=project_id,
                        actor="retention_worker",
                        action="execution_lease_expired",
                        details={"run_id": str(run_id)},
                    )
                )
            for call in session.scalars(
                select(ExecutionCall)
                .where(
                    ExecutionCall.run_id == run_id,
                    ExecutionCall.project_id == project_id,
                    ExecutionCall.state == "reserved",
                    ExecutionCall.lease_until <= now,
                )
                .with_for_update()
            ):
                call.state, call.complete, call.completed_at = "uncertain", False, now
            for episode in session.scalars(
                select(ExecutionEpisode)
                .where(
                    ExecutionEpisode.run_id == run_id,
                    ExecutionEpisode.project_id == project_id,
                    ExecutionEpisode.state == "running",
                )
                .with_for_update()
            ):
                episode.state, episode.outcome, episode.completed_at = "unknown", "outage", now
                episode.summary = {
                    "status": "unknown",
                    "code": "EXECUTION_LEASE_EXPIRED_NO_REPLAY",
                    "score": None,
                }


def money(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("EXECUTION_MONEY_MUST_BE_FINITE_NONNEGATIVE_DECIMAL")
    try:
        if value > Decimal("999999999999") or value != value.quantize(Decimal("0.000000000001")):
            raise ValueError("EXECUTION_MONEY_PRECISION_NOT_QUALIFIED")
    except InvalidOperation:
        raise ValueError("EXECUTION_MONEY_PRECISION_NOT_QUALIFIED") from None
    return value


class ExecutionLedger:
    def __init__(
        self, sessions, run_id, job_id, plan, environment_id, *, authorize, episode_id=None
    ):
        if not callable(authorize):
            raise ValueError("CURRENT_EXECUTION_AUTHORITY_CHECK_REQUIRED")
        self.sessions, self.run_id, self.job_id = sessions, UUID(str(run_id)), UUID(str(job_id))
        self.plan, self.environment_id = plan, UUID(str(environment_id))
        self.authorize, self.episode_id = authorize, UUID(str(episode_id)) if episode_id else None
        self.case_salt = secrets.token_bytes(32)

    def start_episode(self, model, case_key, repeat, max_requests):
        if self.episode_id is not None or not self.plan.episode_execution:
            raise ValueError("EXECUTION_EPISODE_MODE_OR_LIFECYCLE_MISMATCH")
        if type(
            max_requests
        ) is not int or not 1 <= max_requests <= self.plan.episode_execution.max_turns + int(
            self.plan.episode_execution.semantic_judging is not None
        ) + (
            self.plan.episode_execution.user_simulation.max_turns
            if self.plan.episode_execution.user_simulation
            else 0
        ):
            raise ValueError("EXECUTION_EPISODE_REQUEST_BOUND_INVALID")
        identifier = uuid4()
        with self.sessions.begin() as session:
            run, job = self._lock(session)
            if (
                run.status != "RUNNING"
                or job.status != "RUNNING"
                or not job.lease_until
                or job.lease_until <= datetime.now(UTC)
            ):
                raise ValueError("EXECUTION_CANCELLED_OR_LEASE_EXPIRED")
            self.authorize(session)
            count = session.scalar(
                select(func.count())
                .select_from(ExecutionEpisode)
                .where(ExecutionEpisode.project_id == run.project_id)
            )
            if count >= 10000:
                raise ValueError("PROJECT_EXECUTION_EPISODE_QUOTA_EXHAUSTED")
            session.add(
                ExecutionEpisode(
                    id=identifier,
                    project_id=run.project_id,
                    environment_id=self.environment_id,
                    run_id=run.id,
                    model_key=model.key,
                    case_key=hmac.new(
                        self.case_salt, case_key.encode(), hashlib.sha256
                    ).hexdigest(),
                    repeat_index=repeat,
                    execution_version=self.plan.episode_execution.version,
                    max_requests=max_requests,
                )
            )
        self.episode_id = identifier
        return identifier

    def finish_episode(self, observation, assessment):
        with self.sessions.begin() as session:
            run, job = self._lock(session)
            episode = session.get(ExecutionEpisode, self.episode_id, with_for_update=True)
            if (
                not episode
                or episode.run_id != run.id
                or episode.project_id != run.project_id
                or episode.state != "running"
            ):
                raise ValueError("EXECUTION_EPISODE_SCOPE_OR_STATE_MISMATCH")
            if (
                run.status != "RUNNING"
                or job.status != "RUNNING"
                or not job.lease_until
                or job.lease_until <= datetime.now(UTC)
            ):
                raise ValueError("EXECUTION_CANCELLED_OR_LEASE_EXPIRED")
            self.authorize(session)
            episode.state = assessment["status"]
            episode.outcome = observation.outcome
            episode.summary = assessment
            episode.completed_at = datetime.now(UTC)
        self.episode_id = None

    def _lock(self, session):
        project = session.scalar(
            select(ExperimentRunRecord.project_id).where(ExperimentRunRecord.id == self.run_id)
        )
        if not project:
            raise ValueError("EXECUTION_RUN_UNAVAILABLE")
        lock_lineage(session, project)
        run = session.get(ExperimentRunRecord, self.run_id, with_for_update=True)
        job = session.get(JobRecord, self.job_id, with_for_update=True)
        if not run or not job or job.project_id != project or job.run_id != self.run_id:
            raise ValueError("EXECUTION_JOB_SCOPE_MISMATCH")
        return run, job

    def reserve(self, amount, model, purpose):
        amount = money(amount)
        if purpose not in PURPOSES or self.plan.price_sha256 != Catalog().sha256:
            raise ValueError("EXECUTION_PURPOSE_OR_PRICE_CONTRACT_NOT_APPROVED")
        operation = getattr(self.plan, "operation", None)
        if isinstance(getattr(self.plan, "routing_execution", None), RoutingTrainingExecution):
            operation = "routing-training-v1"
        proposal = operation in {
            "candidate-generation-v1",
            "criteria-generation-v1",
            "criteria-generation-v2",
            "scenario-generation-v1",
            "routing-training-v1",
        } or isinstance(getattr(self.plan, "routing_execution", None), RoutingTrainingExecution)
        if (purpose == "optimization") != proposal:
            raise ValueError("EXECUTION_PURPOSE_OUTSIDE_APPROVED_OPERATION")
        targets = [
            target
            for candidate in [
                self.plan.baseline,
                *self.plan.candidates,
                *([self.plan.judge] if self.plan.judge else []),
                *(
                    [self.plan.episode_execution.user_simulation.model]
                    if self.plan.episode_execution and self.plan.episode_execution.user_simulation
                    else []
                ),
            ]
            for target in provider_models(candidate)
        ]
        allowed = next(
            (
                target
                for target in targets
                if (
                    target.key,
                    target.provider,
                    target.model,
                    target.max_input_tokens,
                    target.max_output_tokens,
                    target.region,
                )
                == (
                    model.key,
                    model.provider,
                    model.model,
                    model.max_input_tokens,
                    model.max_output_tokens,
                    model.region,
                )
            ),
            None,
        )
        if not allowed:
            raise ValueError("EXECUTION_MODEL_OUTSIDE_FROZEN_SCOPE")
        semantic = bool(
            self.plan.episode_execution and self.plan.episode_execution.semantic_judging
        )
        simulation = (
            self.plan.episode_execution.user_simulation if self.plan.episode_execution else None
        )
        if (purpose == "simulation") != bool(simulation and model.key == simulation.model.key):
            raise ValueError("SIMULATED_USER_PURPOSE_AND_MODEL_MUST_MATCH_APPROVAL")
        if purpose == "simulation" and self.episode_id is None:
            raise ValueError("SIMULATED_USER_REQUIRES_ACTIVE_TARGET_EPISODE")
        if semantic and ((purpose == "judge") != (model.key == self.plan.judge.key)):
            raise ValueError("EPISODE_JUDGE_PURPOSE_AND_MODEL_MUST_MATCH_APPROVAL")
        if semantic and self.episode_id is None:
            raise ValueError("EPISODE_JUDGE_REQUIRES_ACTIVE_TARGET_EPISODE")
        if allowed.model_dump(mode="json") != model.model_dump(mode="json"):
            raise ValueError("EXECUTION_MODEL_CONFIGURATION_OUTSIDE_FROZEN_SCOPE")
        if allowed.native_contract is not None:
            from cutback.core.native_models import validate_luna_text_contract

            validate_luna_text_contract(allowed, Catalog())
        price_sha256 = (
            allowed.native_contract.price_sha256
            if allowed.native_contract is not None
            else self.plan.price_sha256
        )
        if amount < upper_cost(model):
            raise ValueError("EXECUTION_CALL_RESERVATION_BELOW_UPPER_BOUND")
        if model.agent_turn is not None and not getattr(self.plan, "episode_execution", None):
            raise ValueError("EXECUTION_EPISODE_MODE_NOT_APPROVED")
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            run, job = self._lock(session)
            if (
                run.status != "RUNNING"
                or job.status != "RUNNING"
                or not job.lease_until
                or job.lease_until <= now
            ):
                raise ValueError("EXECUTION_CANCELLED_OR_LEASE_EXPIRED")
            plan_row = session.get(ExperimentPlanRecord, run.plan_id)
            if (
                not plan_row
                or plan_row.specification.get("plan_hash")
                != digest(self.plan.model_dump(mode="json"))
                or plan_row.approval_status != "consumed"
                or principal_role(session, run.project_id, plan_row.approved_by or "")
                not in {"admin", "project_admin"}
            ):
                raise ValueError("EXECUTION_APPROVING_PRINCIPAL_NO_LONGER_AUTHORIZED")
            environment = session.get(Environment, self.environment_id)
            if not environment or environment.project_id != run.project_id:
                raise ValueError("EXECUTION_ENVIRONMENT_SCOPE_MISMATCH")
            self.authorize(session)
            if run.call_ledger_version is None:
                if run.requests_used:
                    raise ValueError("LEGACY_IN_FLIGHT_RUN_CANNOT_GAIN_CALL_RECEIPT_COVERAGE")
                run.call_ledger_version, run.known_cost_requests = 1, 0
            if run.call_ledger_version != 1:
                raise ValueError("EXECUTION_LEDGER_VERSION_UNSUPPORTED")
            if (
                run.requests_used >= self.plan.budget.max_requests
                or run.reserved_usd + amount > self.plan.budget.max_usd
            ):
                raise ValueError("BUDGET_EXHAUSTED")
            count = session.scalar(
                select(func.count())
                .select_from(ExecutionCall)
                .where(ExecutionCall.project_id == run.project_id)
            )
            if count >= MAX_EXECUTION_CALLS:
                raise ValueError("PROJECT_EXECUTION_CALL_QUOTA_EXHAUSTED")
            from cutback.server.campaign_ledger import reserve_campaign_call

            reserve_campaign_call(
                session,
                run,
                self.environment_id,
                digest(self.plan.model_dump(mode="json")),
                purpose,
                amount,
                operation=operation,
            )
            if self.episode_id:
                episode = session.get(ExecutionEpisode, self.episode_id, with_for_update=True)
                if not episode or (
                    episode.run_id,
                    episode.project_id,
                    episode.environment_id,
                    episode.state,
                ) != (run.id, run.project_id, self.environment_id, "running"):
                    raise ValueError("EXECUTION_EPISODE_SCOPE_OR_STATE_MISMATCH")
                if episode.model_key != model.key and not (
                    ((semantic and purpose == "judge") or (simulation and purpose == "simulation"))
                    and episode.model_key
                    in {m.key for m in [self.plan.baseline, *self.plan.candidates]}
                ):
                    raise ValueError("EXECUTION_EPISODE_SCOPE_OR_STATE_MISMATCH")
                if semantic or simulation:
                    used = session.scalar(
                        select(func.count())
                        .select_from(ExecutionCall)
                        .where(
                            ExecutionCall.project_id == run.project_id,
                            ExecutionCall.run_id == run.id,
                            ExecutionCall.episode_id == episode.id,
                            ExecutionCall.purpose == purpose,
                        )
                    )
                    cap = (
                        1
                        if purpose == "judge"
                        else simulation.max_turns
                        if purpose == "simulation" and simulation
                        else episode.max_requests
                        - int(semantic)
                        - (simulation.max_turns if simulation else 0)
                    )
                    if used >= cap:
                        raise ValueError("EXECUTION_EPISODE_PURPOSE_REQUEST_BOUND_EXHAUSTED")
                if episode.requests_used >= episode.max_requests:
                    raise ValueError("EXECUTION_EPISODE_REQUEST_BOUND_EXHAUSTED")
                episode.requests_used += 1
                episode.reserved_usd += amount
            run.requests_used += 1
            run.reserved_usd += amount
            identifier = uuid4()
            session.add(
                ExecutionCall(
                    id=identifier,
                    project_id=run.project_id,
                    environment_id=self.environment_id,
                    run_id=run.id,
                    episode_id=self.episode_id,
                    sequence=run.requests_used,
                    model_key=model.key,
                    provider=model.provider,
                    model=model.model,
                    purpose=purpose,
                    price_sha256=price_sha256,
                    reserved_usd=amount,
                    lease_until=job.lease_until,
                )
            )
            return identifier

    def record(self, identifier, result, status):
        if status not in {"completed", "uncertain", "usage_invalid", "not_started"}:
            raise ValueError("EXECUTION_CALL_RESULT_STATUS_INVALID")
        if (status in {"uncertain", "not_started"} and result is not None) or (
            status == "completed" and result is None
        ):
            raise ValueError("EXECUTION_CALL_RESULT_SHAPE_INVALID")
        spent = Decimal(0) if status == "not_started" else None
        if (
            result is not None
            and isinstance(result.cost_usd, Decimal)
            and result.cost_usd.is_finite()
            and result.cost_usd >= 0
        ):
            spent = money(result.cost_usd)
        if status == "completed" and spent is None:
            raise ValueError("COMPLETED_EXECUTION_CALL_REQUIRES_KNOWN_USAGE")
        tokens = [getattr(result, name, None) for name in ("input_tokens", "output_tokens")]
        tokens = [
            value if type(value) is int and 0 <= value <= 10**12 else None for value in tokens
        ]
        duration = getattr(result, "latency_ms", None)
        duration = (
            float(duration)
            if isinstance(duration, (float, int))
            and not isinstance(duration, bool)
            and math.isfinite(duration)
            and duration >= 0
            else None
        )
        with self.sessions.begin() as session:
            run, job = self._lock(session)
            row = session.get(ExecutionCall, UUID(str(identifier)), with_for_update=True)
            if not row or row.project_id != run.project_id or row.run_id != run.id:
                raise ValueError("EXECUTION_CALL_SCOPE_MISMATCH")
            if row.state != "reserved":
                if (
                    row.spent_usd == spent
                    and row.input_tokens == tokens[0]
                    and row.output_tokens == tokens[1]
                ):
                    return
                if row.spent_usd is not None or spent is None:
                    raise ValueError("EXECUTION_CALL_RESULT_ALREADY_FINAL")
                # A late response can repair costs after an uncertainty receipt;
                # it cannot recover a cancelled/expired episode or approve a change.
            if row.spent_usd is None and spent is not None:
                run.spent_usd += spent
                run.known_cost_requests = (run.known_cost_requests or 0) + 1
                from cutback.server.campaign_ledger import record_campaign_cost

                record_campaign_cost(session, run, spent)
                if row.episode_id:
                    episode = session.get(ExecutionEpisode, row.episode_id, with_for_update=True)
                    if episode:
                        episode.spent_usd += spent
                        episode.known_cost_requests += 1
            now = datetime.now(UTC)
            late = (
                run.status != "RUNNING"
                or job.status != "RUNNING"
                or not job.lease_until
                or job.lease_until <= now
            )
            row.state = (
                "completed_late"
                if late and spent is not None and status != "not_started"
                else status
            )
            row.spent_usd = spent
            row.input_tokens, row.output_tokens = tokens
            row.duration_ms = duration
            row.complete = result.complete if result is not None else None
            row.completed_at = now
