"""Atomic, metadata-only change observations. No SDK, network or deployment calls."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select

from cutback.core.outcome_watches import OutcomeWatchEnvelope, due_window, outcome_review_action
from cutback.server.dataset_lineage import try_lock_lineage
from cutback.server.db import (
    AuditEvent,
    ChangeProposal,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    RecommendationRecord,
)
from cutback.server.economics_api import TaskSnapshotInput, TaskWindow, freeze_task_snapshot
from cutback.server.economics_db import TaskCostSnapshot
from cutback.server.improvement_api import (
    ImprovementInput,
    collect_run_costs,
    freeze_improvement,
    window_identity,
)
from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow
from cutback.server.principals import principal_role
from cutback.server.traces import fingerprint


def source_change_run_family(session, project, change):
    """Compile the retained canonical cost closure; absence is never an empty cost."""
    from cutback.server.campaign_db import CampaignRun

    rec = session.get(RecommendationRecord, change.recommendation_id)
    if not rec or rec.project_id != project:
        raise HTTPException(404, "OUTCOME_WATCH_SOURCE_EXPERIMENT_NOT_FOUND")
    identifiers = {rec.run_id}
    for _ in range(20):
        runs = session.scalars(
            select(ExperimentRunRecord).where(
                ExperimentRunRecord.project_id == project, ExperimentRunRecord.id.in_(identifiers)
            )
        ).all()
        if len(runs) != len(identifiers):
            raise HTTPException(409, "OUTCOME_WATCH_RUN_FAMILY_NOT_RETAINED")
        plans = session.scalars(
            select(ExperimentPlanRecord).where(
                ExperimentPlanRecord.project_id == project,
                ExperimentPlanRecord.id.in_([run.plan_id for run in runs]),
            )
        ).all()
        if len(plans) != len({run.plan_id for run in runs}):
            raise HTTPException(409, "OUTCOME_WATCH_RUN_PLANS_NOT_RETAINED")
        campaigns = set(
            session.scalars(
                select(CampaignRun.campaign_id).where(
                    CampaignRun.project_id == project, CampaignRun.run_id.in_(identifiers)
                )
            ).all()
        )
        expanded = set(identifiers)
        if campaigns:
            expanded.update(
                session.scalars(
                    select(CampaignRun.run_id)
                    .where(
                        CampaignRun.project_id == project, CampaignRun.campaign_id.in_(campaigns)
                    )
                    .limit(21)
                ).all()
            )
        try:
            for plan in plans:
                marker = plan.specification.get("campaign_id")
                if marker and UUID(marker) not in campaigns:
                    raise HTTPException(409, "OUTCOME_WATCH_CAMPAIGN_FAMILY_NOT_RETAINED")
                predecessor = plan.specification.get("public_specification", {}).get(
                    "predecessor_run_id"
                )
                if predecessor:
                    expanded.add(UUID(predecessor))
        except (TypeError, ValueError):
            raise HTTPException(409, "OUTCOME_WATCH_RUN_LINEAGE_INVALID") from None
        if len(expanded) > 20:
            raise HTTPException(409, "OUTCOME_WATCH_CANONICAL_RUN_FAMILY_EXCEEDS_BOUND")
        if expanded == identifiers:
            ordered = sorted(identifiers)
            _, missing = collect_run_costs(session, project, ordered, change)
            if missing:
                raise HTTPException(409, "OUTCOME_WATCH_RUN_FAMILY_NOT_RETAINED")
            return ordered
        identifiers = expanded
    raise HTTPException(409, "OUTCOME_WATCH_RUN_LINEAGE_EXCEEDS_BOUND")


def audit(session, row, actor, action, details=None):
    session.add(
        AuditEvent(
            project_id=row.project_id,
            actor=actor,
            action=action,
            details={"watch_id": str(row.id), **(details or {})},
        )
    )


def context(session, project, environment, spec):
    baseline = session.get(TaskCostSnapshot, spec.baseline_snapshot_id)
    if not baseline or (baseline.project_id, baseline.environment_id) != (project, environment):
        raise HTTPException(404, "OUTCOME_WATCH_BASELINE_NOT_FOUND")
    if (
        baseline.evidence_sha256 != spec.baseline_sha256
        or fingerprint(baseline.report) != spec.baseline_sha256
    ):
        raise HTTPException(409, "OUTCOME_WATCH_BASELINE_INTEGRITY_FAILURE")
    window = TaskWindow.model_validate(baseline.report)
    workflow, service, release = window_identity(window)
    if release == spec.candidate_release:
        raise HTTPException(409, "OUTCOME_WATCH_REQUIRES_DISTINCT_CANDIDATE_RELEASE")
    if spec.first_window_end - timedelta(seconds=spec.window_seconds) < window.end:
        raise HTTPException(409, "OUTCOME_WATCH_CANDIDATE_MUST_FOLLOW_BASELINE_WINDOW")
    change = session.get(ChangeProposal, spec.change_id)
    if not change or change.project_id != project:
        raise HTTPException(404, "OUTCOME_WATCH_CHANGE_NOT_FOUND")
    if change.evidence.get("diff_sha256") != spec.source_diff_sha256:
        raise HTTPException(409, "OUTCOME_WATCH_SOURCE_CHANGE_HASH_MISMATCH")
    _, missing = collect_run_costs(session, project, spec.optimization_run_ids, change)
    if missing:
        raise HTTPException(409, "OUTCOME_WATCH_CANONICAL_RUN_FAMILY_NOT_RETAINED_OR_COMPLETE")
    return workflow, service


def blocked(session, row, code, now):
    row.enabled, row.status, row.last_code, row.updated_at = False, "BLOCKED", code, now
    audit(session, row, "outcome_watch_worker", "outcome_watch_blocked", {"code": code})
    return {"status": "BLOCKED", "code": code, "watch_id": str(row.id), "provider_calls": 0}


def safe_code(error):
    code = error.detail if isinstance(error, HTTPException) else "OUTCOME_WATCH_CONTEXT_INVALID"
    return (
        code
        if isinstance(code, str) and code.isascii() and code.isupper() and len(code) <= 100
        else "OUTCOME_WATCH_CONTEXT_INVALID"
    )


def process(session, sessions, row, now):
    if fingerprint(row.specification) != row.approval_hash:
        return blocked(session, row, "OUTCOME_WATCH_APPROVED_HASH_CHANGED", now)
    try:
        envelope = OutcomeWatchEnvelope.model_validate(row.specification)
    except ValueError:
        return blocked(session, row, "OUTCOME_WATCH_SPECIFICATION_INVALID", now)
    if (envelope.project_id, envelope.environment_id) != (row.project_id, row.environment_id):
        return blocked(session, row, "OUTCOME_WATCH_APPROVED_SCOPE_CHANGED", now)
    spec = envelope.policy
    if principal_role(session, row.project_id, row.approved_by or "") not in {
        "admin",
        "project_admin",
    }:
        return blocked(session, row, "OUTCOME_WATCH_APPROVING_PRINCIPAL_WITHDRAWN", now)
    if now >= spec.expires_at or row.windows_processed >= spec.max_windows:
        row.enabled = False
        row.status = "EXPIRED" if now >= spec.expires_at else "EXHAUSTED"
        row.updated_at = now
        audit(session, row, "outcome_watch_worker", "outcome_watch_closed", {"state": row.status})
        return {"status": row.status, "watch_id": str(row.id), "provider_calls": 0}
    window = due_window(spec, now, row.last_window_index)
    if window is None:
        return {"status": "IDLE", "provider_calls": 0}
    try:
        workflow, service = context(session, row.project_id, row.environment_id, spec)
    except (HTTPException, ValueError) as error:
        return blocked(session, row, safe_code(error), now)
    count = session.scalar(
        select(func.count())
        .select_from(OutcomeWatchWindow)
        .where(OutcomeWatchWindow.project_id == row.project_id)
    )
    if count >= 10000:
        return blocked(session, row, "OUTCOME_WATCH_PROJECT_RECEIPT_QUOTA", now)
    candidate, improvement = None, None
    code = None
    action = "COLLECT_MISSING_EVIDENCE"
    reasons = ["candidate_window_has_no_declared_tasks"]
    status = "MISSING_EVIDENCE"
    report = None
    try:
        candidate = freeze_task_snapshot(
            session,
            sessions,
            row.project_id,
            row.environment_id,
            TaskSnapshotInput(
                snapshot_id=uuid5(row.id, f"candidate-window:{window['index']}"),
                start=window["start"],
                end=window["end"],
                workflow=workflow,
                service=service,
                release=spec.candidate_release,
                expected_tasks=spec.expected_tasks,
                as_of=now,
            ),
            "outcome_watch_worker",
        )
        if candidate.report.summary.submitted_tasks:
            improvement = freeze_improvement(
                session,
                row.project_id,
                ImprovementInput(
                    observation_id=uuid5(row.id, f"improvement-window:{window['index']}"),
                    baseline_snapshot_id=spec.baseline_snapshot_id,
                    candidate_snapshot_id=candidate.id,
                    change_id=spec.change_id,
                    release_linkage_attested=True,
                    optimization_run_ids=spec.optimization_run_ids,
                    assumptions=spec.assumptions,
                ),
                "outcome_watch_worker",
            )
            report = improvement.evidence.report.model_dump(mode="json")
            reasons, action, status = report["reasons"], outcome_review_action(report), "COMPLETED"
    except HTTPException as error:
        code = safe_code(error)
        status, reasons = "BLOCKED", [code]
        blocked(session, row, code, now)
    # Metadata calculation can overlap a key or membership revocation. Use a
    # fresh reader, not the identity-cached principal from the opening check.
    with sessions() as authority:
        if principal_role(authority, row.project_id, row.approved_by or "") not in {
            "admin",
            "project_admin",
        }:
            raise ValueError("OUTCOME_WATCH_AUTHORITY_CHANGED_DURING_CALCULATION")
    receipt = {
        "contract": "cutback.outcome-watch-window.v1",
        "status": status,
        "approval_hash": row.approval_hash,
        "window_index": window["index"],
        "start": window["start"].isoformat(),
        "end": window["end"].isoformat(),
        "declarations_as_of": now.isoformat(),
        "candidate_snapshot_id": str(candidate.id) if candidate else None,
        "improvement_id": str(improvement.id) if improvement else None,
        "report": report,
        "next_action": action,
        "reasons": reasons,
        "skipped_previous_windows": window["skipped"],
        "release_linkage": "owner_attested_not_deployment_verified",
        "action_authorized": False,
        "provider_calls": 0,
        "traffic_changed": False,
    }
    item = OutcomeWatchWindow(
        project_id=row.project_id,
        environment_id=row.environment_id,
        watch_record_id=row.id,
        window_index=window["index"],
        window_start=window["start"],
        window_end=window["end"],
        candidate_snapshot_id=candidate.id if candidate else None,
        improvement_id=improvement.id if improvement else None,
        evidence_sha256=fingerprint(receipt),
        receipt=receipt,
    )
    session.add(item)
    row.last_window_index = window["index"]
    row.windows_skipped += window["skipped"]
    row.windows_processed += 1
    row.next_run_at = window["next_due"]
    row.last_code, row.updated_at = code, now
    if row.windows_processed >= spec.max_windows and row.status != "BLOCKED":
        row.status, row.enabled = "EXHAUSTED", False
    session.flush()
    audit(
        session,
        row,
        "outcome_watch_worker",
        "outcome_watch_window_observed",
        {
            "window_receipt_id": str(item.id),
            "window_index": window["index"],
            "next_action": action,
            "evidence_sha256": item.evidence_sha256,
        },
    )
    return {
        "status": status,
        "watch_id": str(row.id),
        "window_receipt_id": str(item.id),
        "next_action": action,
        "provider_calls": 0,
    }


def run_outcome_watch_one(sessions, *, enabled=False, now=None, only_watch_id=None):
    if not enabled:
        return {"status": "OUTCOME_WATCH_EXECUTION_DISABLED", "provider_calls": 0}
    now = now or datetime.now(UTC)
    with sessions() as reader:
        query = select(OutcomeWatch.project_id).where(
            OutcomeWatch.enabled.is_(True),
            OutcomeWatch.status == "APPROVED",
            OutcomeWatch.next_run_at <= now,
        )
        if only_watch_id is not None:
            query = query.where(OutcomeWatch.id == UUID(str(only_watch_id)))
        candidates = reader.scalars(
            query.group_by(OutcomeWatch.project_id)
            .order_by(func.min(OutcomeWatch.next_run_at), OutcomeWatch.project_id)
            .limit(20)
        ).all()
    for project in candidates:
        with sessions.begin() as session:
            # Match the API's advisory-lock order without waiting on a busy tenant.
            if not try_lock_lineage(session, project):
                continue
            eligible = select(OutcomeWatch).where(
                OutcomeWatch.project_id == project,
                OutcomeWatch.enabled.is_(True),
                OutcomeWatch.status == "APPROVED",
                OutcomeWatch.next_run_at <= now,
            )
            if only_watch_id is not None:
                eligible = eligible.where(OutcomeWatch.id == UUID(str(only_watch_id)))
            row = session.scalar(
                eligible.order_by(OutcomeWatch.next_run_at, OutcomeWatch.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is not None:
                return process(session, sessions, row, now)
    return {"status": "IDLE", "provider_calls": 0}
