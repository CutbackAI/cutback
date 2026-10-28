"""Frozen descriptive change observations. No model calls, traffic changes or deployment."""

import re
from datetime import datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import Field, StrictBool, model_validator
from sqlalchemy import func, select

from cutback.core.improvement_economics import (
    ImprovementAssumptions,
    OptimizationRunCost,
    SearchEffortInput,
    choose_search_effort,
    compare_improvement_economics,
)
from cutback.core.models import StrictModel
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import (
    AuditEvent,
    ChangeProposal,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    RecommendationRecord,
)
from cutback.server.economics_api import TaskWindow
from cutback.server.economics_db import ImprovementObservation, TaskCostSnapshot
from cutback.server.execution_db import ExecutionCall
from cutback.server.traces import fingerprint


class ImprovementInput(StrictModel):
    observation_id: UUID
    baseline_snapshot_id: UUID
    candidate_snapshot_id: UUID
    change_id: UUID | None = None
    release_linkage_attested: StrictBool = False
    optimization_run_ids: list[UUID] = Field(default_factory=list, max_length=20)
    assumptions: ImprovementAssumptions = Field(default_factory=ImprovementAssumptions)

    @model_validator(mode="after")
    def valid(self):
        if not self.observation_id.int or self.baseline_snapshot_id == self.candidate_snapshot_id:
            raise ValueError("DISTINCT_IMPROVEMENT_IDENTITIES_REQUIRED")
        if len(set(self.optimization_run_ids)) != len(self.optimization_run_ids):
            raise ValueError("DUPLICATE_OPTIMIZATION_RUN_IDENTITIES")
        if self.change_id and not self.release_linkage_attested:
            raise ValueError("SOURCE_CHANGE_RELEASE_LINKAGE_ATTESTATION_REQUIRED")
        return self


class ImprovementReport(StrictModel):
    contract: str
    decision: str
    reasons: list[str]
    baseline_declared_success_rate: str | None
    candidate_declared_success_rate: str | None
    descriptive_outcome_non_regression: bool | None
    modeled_cost_per_success_delta_usd: str | None
    known_optimization_run_subtotal_usd: str | None
    known_other_optimization_subtotal_usd: str | None
    optimization_cost_complete_under_declaration: bool
    declared_optimization_total_usd: str | None
    forecast_successful_tasks: int | None
    forecast_net_modeled_benefit_usd: str | None
    forecast_break_even_successful_tasks: str | None
    forecast_evidence: str
    quality_evidence: str
    causal_attribution: str
    production_savings_usd: None = None
    deployment_authorized: bool = False


class ImprovementEvidence(StrictModel):
    report: ImprovementReport
    assumptions: ImprovementAssumptions
    baseline_snapshot_id: UUID
    baseline_sha256: str
    candidate_snapshot_id: UUID
    candidate_sha256: str
    change_id: UUID | None
    source_diff_sha256: str | None
    release_linkage: str
    workflow: str
    service: str
    baseline_release: str
    candidate_release: str
    run_costs: list[OptimizationRunCost]
    missing_predecessor_costs: int
    run_set_evidence: str = "owner_declared_run_set_canonical_cumulative_ledger_totals"


class ImprovementReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    evidence_sha256: str
    evidence: ImprovementEvidence
    created_at: datetime


class ImprovementPage(StrictModel):
    items: list[ImprovementReceipt]
    next_after: UUID | None


class SearchEffortReceipt(StrictModel):
    decision: str
    candidate_limit: int
    forecast_gross_modeled_opportunity_usd: str | None
    suggested_total_spend_cap_usd: str | None
    reasons: list[str]
    evidence: str
    paid_calls_authorized: bool
    provider_calls: int


def receipt(row):
    return ImprovementReceipt(
        id=row.id,
        environment_id=row.environment_id,
        evidence_sha256=row.evidence_sha256,
        evidence=ImprovementEvidence.model_validate(row.receipt),
        created_at=row.created_at,
    )


def window_identity(window):
    identities = {
        (item.declaration.workflow, item.declaration.service, item.declaration.release)
        for item in window.items
    }
    if len(identities) != 1 or next(iter(identities))[2] is None:
        raise HTTPException(409, "IMPROVEMENT_REQUIRES_ONE_WORKFLOW_SERVICE_RELEASE_PER_WINDOW")
    return next(iter(identities))


def collect_run_costs(session, project, run_ids, change):
    runs = session.scalars(
        select(ExperimentRunRecord).where(
            ExperimentRunRecord.project_id == project,
            ExperimentRunRecord.id.in_(run_ids),
        )
    ).all()
    if len(runs) != len(run_ids):
        raise HTTPException(404, "OPTIMIZATION_RUN_NOT_FOUND")
    if change:
        rec = session.get(RecommendationRecord, change.recommendation_id)
        if not rec or rec.project_id != project or rec.run_id not in run_ids:
            raise HTTPException(409, "INCLUDE_SOURCE_CHANGE_EXPERIMENT_COST")
    unresolved = dict(
        session.execute(
            select(ExecutionCall.run_id, func.count())
            .where(
                ExecutionCall.project_id == project,
                ExecutionCall.run_id.in_(run_ids),
                ExecutionCall.state.in_(["reserved", "uncertain"]),
            )
            .group_by(ExecutionCall.run_id)
        ).all()
    )
    plans = session.scalars(
        select(ExperimentPlanRecord).where(
            ExperimentPlanRecord.project_id == project,
            ExperimentPlanRecord.id.in_([r.plan_id for r in runs]),
        )
    ).all()
    missing: set[UUID] = set()
    from cutback.server.campaign_db import CampaignRun

    campaigns = select(CampaignRun.campaign_id).where(
        CampaignRun.project_id == project, CampaignRun.run_id.in_(run_ids)
    )
    known_campaigns = set(session.scalars(campaigns).all())
    siblings = session.scalars(
        select(CampaignRun.run_id)
        .where(CampaignRun.project_id == project, CampaignRun.campaign_id.in_(known_campaigns))
        .limit(141)
    ).all()
    if len(siblings) > 140:
        raise HTTPException(409, "OPTIMIZATION_CAMPAIGN_COST_CLOSURE_EXCEEDS_BOUND")
    missing.update(identifier for identifier in siblings if identifier not in run_ids)
    for plan in plans:
        marker = plan.specification.get("campaign_id")
        if marker:
            try:
                campaign_id = UUID(marker)
            except (ValueError, TypeError):
                raise HTTPException(409, "OPTIMIZATION_CAMPAIGN_COST_CONTEXT_INVALID") from None
            if campaign_id not in known_campaigns:
                # Retention removed the family relation. Individual run totals
                # cannot reconstruct missing parent/sibling coverage.
                missing.add(campaign_id)
        predecessor = plan.specification.get("public_specification", {}).get("predecessor_run_id")
        if predecessor and UUID(predecessor) not in run_ids:
            missing.add(UUID(predecessor))
    return [
        OptimizationRunCost(
            run_id=r.id,
            status=r.status,
            requests_reserved=r.requests_used,
            known_cost_requests=r.known_cost_requests,
            known_cost_subtotal_usd=r.spent_usd,
            ledger_version=r.call_ledger_version,
            uncertain_or_reserved_calls=unresolved.get(r.id, 0),
        )
        for r in sorted(runs, key=lambda r: r.id)
    ], len(missing)


def install_improvements(app, sessions, authorize, scope):
    @app.post("/v1/projects/{project_id}/search-effort", response_model=SearchEffortReceipt)
    def effort(project_id: UUID, body: SearchEffortInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        return SearchEffortReceipt.model_validate(choose_search_effort(body))

    @app.post(
        "/v1/projects/{project_id}/improvement-observations", response_model=ImprovementReceipt
    )
    def observe(project_id: UUID, body: ImprovementInput, auth=Depends(authorize)):
        scope(auth, project_id, ("analyst",))
        specification = body.model_dump(mode="json")
        request_hash = fingerprint(specification)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            existing = session.scalar(
                select(ImprovementObservation).where(
                    ImprovementObservation.project_id == project_id,
                    ImprovementObservation.observation_id == body.observation_id,
                )
            )
            if existing:
                if existing.request_sha256 != request_hash:
                    raise HTTPException(409, "IMPROVEMENT_OBSERVATION_REQUEST_CONFLICT")
                return receipt(existing)
            if (
                session.scalar(
                    select(func.count())
                    .select_from(ImprovementObservation)
                    .where(
                        ImprovementObservation.project_id == project_id,
                    )
                )
                >= 1000
            ):
                raise HTTPException(409, "IMPROVEMENT_OBSERVATION_PROJECT_QUOTA")
            baseline = session.get(TaskCostSnapshot, body.baseline_snapshot_id)
            candidate = session.get(TaskCostSnapshot, body.candidate_snapshot_id)
            if (
                not baseline
                or not candidate
                or baseline.project_id != project_id
                or candidate.project_id != project_id
            ):
                raise HTTPException(404, "TASK_SNAPSHOT_NOT_FOUND")
            snapshots = [baseline, candidate]
            if baseline.environment_id != candidate.environment_id:
                raise HTTPException(409, "IMPROVEMENT_ENVIRONMENT_MISMATCH")
            if any(fingerprint(s.report) != s.evidence_sha256 for s in snapshots):
                raise HTTPException(409, "TASK_SNAPSHOT_INTEGRITY_FAILURE")
            before, after = (TaskWindow.model_validate(s.report) for s in snapshots)
            base_identity, candidate_identity = window_identity(before), window_identity(after)
            if (
                base_identity[:2] != candidate_identity[:2]
                or base_identity[2] == candidate_identity[2]
            ):
                raise HTTPException(409, "IMPROVEMENT_REQUIRES_SAME_WORKFLOW_AND_DISTINCT_RELEASES")
            if {item.declaration.task_id for item in before.items} & {
                item.declaration.task_id for item in after.items
            }:
                raise HTTPException(409, "IMPROVEMENT_WINDOWS_CANNOT_REUSE_A_TASK")
            change = session.get(ChangeProposal, body.change_id) if body.change_id else None
            if body.change_id and (not change or change.project_id != project_id):
                raise HTTPException(404, "CHANGE_NOT_FOUND")
            if change and not re.fullmatch(r"[a-f0-9]{64}", change.evidence.get("diff_sha256", "")):
                raise HTTPException(409, "SOURCE_BOUND_CHANGE_DIFF_REQUIRED")
            costs, missing = collect_run_costs(
                session, project_id, body.optimization_run_ids, change
            )
            if missing and body.assumptions.all_optimization_runs_declared_complete:
                raise HTTPException(409, "INCLUDE_PREDECESSOR_OPTIMIZATION_RUN_COSTS")
            result = compare_improvement_economics(
                before.summary, after.summary, costs, body.assumptions
            )
            evidence = ImprovementEvidence(
                report=ImprovementReport.model_validate(result),
                assumptions=body.assumptions,
                baseline_snapshot_id=baseline.id,
                baseline_sha256=baseline.evidence_sha256,
                candidate_snapshot_id=candidate.id,
                candidate_sha256=candidate.evidence_sha256,
                change_id=body.change_id,
                source_diff_sha256=change.evidence.get("diff_sha256") if change else None,
                release_linkage="owner_attested_not_deployment_verified"
                if change
                else "unlinked_window_comparison",
                workflow=base_identity[0],
                service=base_identity[1],
                baseline_release=base_identity[2],
                candidate_release=candidate_identity[2],
                run_costs=costs,
                missing_predecessor_costs=missing,
            ).model_dump(mode="json")
            row = ImprovementObservation(
                project_id=project_id,
                environment_id=baseline.environment_id,
                observation_id=body.observation_id,
                change_id=body.change_id,
                baseline_snapshot_id=baseline.id,
                candidate_snapshot_id=candidate.id,
                request_sha256=request_hash,
                evidence_sha256=fingerprint(evidence),
                specification=specification,
                receipt=evidence,
                actor=auth["actor"],
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="improvement_observed",
                    details={
                        "observation_id": str(row.id),
                        "evidence_sha256": row.evidence_sha256,
                        "decision": result["decision"],
                    },
                )
            )
            return receipt(row)

    @app.get("/v1/projects/{project_id}/improvement-observations", response_model=ImprovementPage)
    def observations(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(20, ge=1, le=50),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(ImprovementObservation).where(
                ImprovementObservation.project_id == project_id
            )
            if after:
                query = query.where(ImprovementObservation.id > after)
            rows = session.scalars(query.order_by(ImprovementObservation.id).limit(limit + 1)).all()
            return ImprovementPage(
                items=[receipt(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )
