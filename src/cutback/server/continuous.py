"""Explicitly approved bounded schedules and operator-reported rollout evidence. No deployment."""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException
from pydantic import Field, model_validator
from sqlalchemy import select

from cutback.changes import CanaryPolicy, canary_decision
from cutback.core.models import Identifier, StrictModel
from cutback.evals.datasets import digest
from cutback.pricing import Catalog
from cutback.server.db import (
    AuditEvent,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    RecommendationRecord,
)
from cutback.server.enterprise_db import ExperimentSchedule, RolloutRecord


class ScheduleInput(StrictModel):
    plan_id: UUID
    interval_seconds: int = Field(ge=60, le=2592000)
    max_runs: int = Field(ge=1, le=100)
    max_total_usd: Decimal = Field(ge=0, le=100000)
    max_total_requests: int = Field(ge=1, le=1000000)
    first_run_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def times(self):
        if (
            not self.first_run_at.tzinfo
            or not self.expires_at.tzinfo
            or not self.first_run_at < self.expires_at
        ):
            raise ValueError("aware ordered schedule times required")
        if self.expires_at - self.first_run_at > timedelta(days=90):
            raise ValueError("schedule window exceeds ninety days")
        return self


class Approval(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: Identifier


class RolloutInput(StrictModel):
    recommendation_id: UUID
    environment: Literal["staging", "production"] = "staging"
    baseline_ref: Identifier
    rollback_ref: Identifier
    max_traffic_percent: float = Field(default=1, gt=0, le=50)
    max_usd: Decimal = Field(default=Decimal(0), ge=0, le=10000)
    guardrails: CanaryPolicy = Field(default_factory=CanaryPolicy)


class RolloutTransition(StrictModel):
    state: Literal["DEPLOYED_CANARY", "REJECTED", "ROLLED_BACK"]
    operator_ref: Identifier


class Window(StrictModel):
    observations: int = Field(ge=1, le=100000000)
    error_rate: float = Field(ge=0, le=1)
    schema_failure_rate: float = Field(ge=0, le=1)
    latency_p95_ms: float | None = Field(default=None, ge=0)
    cost_per_request_usd: float | None = Field(default=None, ge=0)
    business_quality: float | None = Field(default=None, ge=0, le=1)


class RolloutObservation(StrictModel):
    start: datetime
    end: datetime
    baseline: Window
    candidate: Window
    evidence_ref: Identifier
    controlled: bool = False
    independent_business_metric: bool = False
    synthetic: bool = False

    @model_validator(mode="after")
    def valid_period(self):
        if (
            not self.start.tzinfo
            or not self.end.tzinfo
            or not self.start < self.end <= datetime.now(UTC) + timedelta(minutes=5)
        ):
            raise ValueError("aware ordered observation period required")
        if self.end - self.start > timedelta(days=30):
            raise ValueError("observation period exceeds thirty days")
        return self


def audit(session, project, actor, action, details):
    session.add(AuditEvent(project_id=project, actor=actor, action=action, details=details))


def queue_schedules(sessions, *, enabled=False, now=None):
    if not enabled:
        return {"status": "SCHEDULE_EXECUTION_DISABLED", "queued": 0}
    now = now or datetime.now(UTC)
    queued = 0
    with sessions.begin() as session:
        rows = session.scalars(
            select(ExperimentSchedule)
            .where(
                ExperimentSchedule.enabled.is_(True),
                ExperimentSchedule.next_run_at <= now,
            )
            .order_by(ExperimentSchedule.next_run_at)
            .with_for_update(skip_locked=True)
            .limit(10)
        ).all()
        for row in rows:
            spec = row.specification
            plan = session.get(ExperimentPlanRecord, row.plan_id)
            current_price = Catalog()
            valid = (
                digest(spec) == row.approval_hash
                and now < datetime.fromisoformat(spec["expires_at"])
                and row.runs_queued < spec["max_runs"]
                and plan is not None
                and plan.project_id == row.project_id
                and plan.specification["plan_hash"] == spec["plan_hash"]
                and plan.specification["public_specification"].get("price_sha256")
                == current_price.sha256
                and now < datetime.fromisoformat(plan.specification["expires_at"])
            )
            if not valid:
                row.enabled = False
                audit(
                    session,
                    row.project_id,
                    "scheduler",
                    "schedule_blocked_or_exhausted",
                    {"schedule_id": str(row.id)},
                )
                continue
            cap = Decimal(plan.specification["public_specification"]["budget"]["max_usd"])
            requests = plan.specification["public_specification"]["budget"]["max_requests"]
            if (
                row.reserved_usd + cap > Decimal(spec["max_total_usd"])
                or (row.runs_queued + 1) * requests > spec["max_total_requests"]
            ):
                row.enabled = False
                continue
            row.reserved_usd += cap
            row.runs_queued += 1
            row.next_run_at = now + timedelta(seconds=spec["interval_seconds"])
            row.enabled = row.runs_queued < spec["max_runs"]
            record = ExperimentRunRecord(
                project_id=row.project_id, plan_id=row.plan_id, status="QUEUED", results={}
            )
            session.add(record)
            session.flush()
            session.add(JobRecord(project_id=row.project_id, run_id=record.id, status="QUEUED"))
            audit(
                session,
                row.project_id,
                "scheduler",
                "scheduled_experiment_queued",
                {
                    "schedule_id": str(row.id),
                    "run_id": str(record.id),
                    "reserved_schedule_usd": str(row.reserved_usd),
                    "approval_ref": row.approval_ref,
                },
            )
            queued += 1
    return {"status": "PROCESSED", "queued": queued, "provider_calls": 0}


def install_continuous(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/schedules")
    def schedules(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "specification": row.specification,
                    "approval_hash": row.approval_hash,
                    "enabled": row.enabled,
                    "runs_queued": row.runs_queued,
                    "reserved_usd": str(row.reserved_usd),
                    "next_run_at": row.next_run_at.isoformat(),
                    "scheduler_enabled": os.getenv("CUTBACK_SCHEDULE_EXECUTION", "false").lower()
                    == "true",
                }
                for row in session.scalars(
                    select(ExperimentSchedule)
                    .where(ExperimentSchedule.project_id == project_id)
                    .limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/schedules", status_code=201)
    def create_schedule(project_id: UUID, body: ScheduleInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            plan = session.get(ExperimentPlanRecord, body.plan_id)
            if not plan or plan.project_id != project_id:
                raise HTTPException(404, "PLAN_NOT_FOUND")
            budget = plan.specification["public_specification"]["budget"]
            if (
                Decimal(budget["max_usd"]) * body.max_runs > body.max_total_usd
                or budget["max_requests"] * body.max_runs > body.max_total_requests
            ):
                raise HTTPException(422, "SCHEDULE_AGGREGATE_BUDGET_TOO_SMALL")
            spec = {**body.model_dump(mode="json"), "plan_hash": plan.specification["plan_hash"]}
            row = ExperimentSchedule(
                project_id=project_id,
                plan_id=body.plan_id,
                specification=spec,
                approval_hash=digest(spec),
                approval_ref="pending",
                approved_by=auth["actor"],
                next_run_at=body.first_run_at,
            )
            session.add(row)
            session.flush()
            audit(
                session,
                project_id,
                auth["actor"],
                "schedule_proposed",
                {"schedule_id": str(row.id), "approval_hash": row.approval_hash},
            )
        return {
            "id": str(row.id),
            "approval_hash": row.approval_hash,
            "enabled": False,
            "specification": spec,
        }

    @app.post("/v1/projects/{project_id}/schedules/{schedule_id}/approve")
    def approve_schedule(
        project_id: UUID, schedule_id: UUID, body: Approval, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(ExperimentSchedule, schedule_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "SCHEDULE_NOT_FOUND")
            if (
                row.approval_ref != "pending"
                or row.approval_hash != body.approval_hash
                or digest(row.specification) != body.approval_hash
            ):
                raise HTTPException(409, "SCHEDULE_APPROVAL_MISMATCH_OR_ALREADY_USED")
            if datetime.fromisoformat(row.specification["expires_at"]) <= datetime.now(UTC):
                raise HTTPException(409, "SCHEDULE_EXPIRED")
            row.enabled, row.approval_ref, row.approved_by = True, body.approval_ref, auth["actor"]
            audit(
                session,
                project_id,
                auth["actor"],
                "schedule_approved",
                {
                    "schedule_id": str(row.id),
                    "approval_ref": body.approval_ref,
                    "approval_hash": body.approval_hash,
                },
            )
        return {
            "status": "approved",
            "execution": "requires explicitly enabled worker",
            "provider_calls": 0,
        }

    @app.delete("/v1/projects/{project_id}/schedules/{schedule_id}")
    def disable_schedule(project_id: UUID, schedule_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(ExperimentSchedule, schedule_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "SCHEDULE_NOT_FOUND")
            row.enabled = False
            audit(
                session,
                project_id,
                auth["actor"],
                "schedule_disabled",
                {"schedule_id": str(row.id)},
            )
        return {"status": "disabled", "queued_runs": "cancel separately if required"}

    @app.get("/v1/projects/{project_id}/rollouts")
    def rollouts(project_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            return [
                {
                    "id": str(row.id),
                    "status": row.status,
                    "specification": row.specification,
                    "observations": row.observations,
                    "approval_hash": digest(row.specification),
                }
                for row in session.scalars(
                    select(RolloutRecord).where(RolloutRecord.project_id == project_id).limit(100)
                )
            ]

    @app.post("/v1/projects/{project_id}/rollouts", status_code=201)
    def create_rollout(project_id: UUID, body: RolloutInput, auth=Depends(authorize)):
        scope(auth, project_id, ("editor",))
        with sessions.begin() as session:
            rec = session.get(RecommendationRecord, body.recommendation_id)
            if not rec or rec.project_id != project_id or rec.status != "accepted_for_staging":
                raise HTTPException(409, "REVIEWED_RECOMMENDATION_REQUIRED")
            row = RolloutRecord(
                project_id=project_id,
                recommendation_id=rec.id,
                specification=body.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            audit(
                session,
                project_id,
                auth["actor"],
                "rollout_proposed",
                {"rollout_id": str(row.id), "approval_hash": digest(row.specification)},
            )
        return {
            "id": str(row.id),
            "status": "PLANNED",
            "approval_hash": digest(row.specification),
            "traffic_changed": False,
        }

    @app.post("/v1/projects/{project_id}/rollouts/{rollout_id}/approve")
    def approve_rollout(
        project_id: UUID, rollout_id: UUID, body: Approval, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(RolloutRecord, rollout_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "ROLLOUT_NOT_FOUND")
            if row.status != "PLANNED" or digest(row.specification) != body.approval_hash:
                raise HTTPException(409, "ROLLOUT_STATE_OR_HASH_MISMATCH")
            row.status, row.approved_by = "APPROVED", auth["actor"]
            audit(
                session,
                project_id,
                auth["actor"],
                "rollout_approved",
                {
                    "rollout_id": str(row.id),
                    "approval_ref": body.approval_ref,
                    "approval_hash": body.approval_hash,
                },
            )
        return {"status": "APPROVED", "traffic_changed": False}

    @app.post("/v1/projects/{project_id}/rollouts/{rollout_id}/transition")
    def transition(
        project_id: UUID, rollout_id: UUID, body: RolloutTransition, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(RolloutRecord, rollout_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "ROLLOUT_NOT_FOUND")
            allowed = {
                "DEPLOYED_CANARY": {"APPROVED"},
                "REJECTED": {"PLANNED", "APPROVED"},
                "ROLLED_BACK": {"DEPLOYED_CANARY", "ROLLBACK_RECOMMENDED", "VALIDATED"},
            }
            if row.status not in allowed[body.state]:
                raise HTTPException(409, "INVALID_ROLLOUT_TRANSITION")
            row.status = body.state
            audit(
                session,
                project_id,
                auth["actor"],
                "operator_reported_rollout_transition",
                {"rollout_id": str(row.id), **body.model_dump()},
            )
        return {
            "status": body.state,
            "evidence": "operator_reported",
            "traffic_changed_by_cutback": False,
        }

    @app.post("/v1/projects/{project_id}/rollouts/{rollout_id}/observations")
    def observations(
        project_id: UUID, rollout_id: UUID, body: RolloutObservation, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            row = session.get(RolloutRecord, rollout_id, with_for_update=True)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "ROLLOUT_NOT_FOUND")
            if (
                row.status not in {"DEPLOYED_CANARY", "ROLLBACK_RECOMMENDED", "VALIDATED"}
                or len(row.observations) >= 100
            ):
                raise HTTPException(409, "ROLLOUT_NOT_ACTIVE_OR_OBSERVATION_LIMIT")
            decision = canary_decision(
                CanaryPolicy.model_validate(row.specification["guardrails"]),
                body.baseline.model_dump(),
                body.candidate.model_dump(),
            )
            tier5 = (
                decision["state"] == "GUARDRAILS_PASSED"
                and body.controlled
                and body.independent_business_metric
                and not body.synthetic
                and row.specification["environment"] == "production"
                and row.specification["guardrails"].get("minimum_business_quality") is not None
            )
            entry = {
                **body.model_dump(mode="json"),
                "decision": decision,
                "evidence_tier": 5 if tier5 else 1,
                "provenance": "operator_reported_not_independently_audited",
            }
            row.observations = [*row.observations, entry]
            if decision["state"] == "ROLLBACK_RECOMMENDED":
                row.status = "ROLLBACK_RECOMMENDED"
            elif tier5:
                row.status = "VALIDATED"
            audit(
                session,
                project_id,
                auth["actor"],
                "rollout_observation_recorded",
                {
                    "rollout_id": str(row.id),
                    "evidence_ref": body.evidence_ref,
                    "evidence_tier": entry["evidence_tier"],
                    "decision": decision["state"],
                },
            )
        return {**entry, "status": row.status, "traffic_changed": False}
