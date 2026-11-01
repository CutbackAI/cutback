"""Atomic task revisions and bounded, retained-evidence cost joins.

Business results are producer assertions. A structurally valid trace cannot prove
complete capture, billing accuracy, or a representative production population.
"""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import BigInteger, DateTime, String, cast, delete, exists, func, or_, select, update

from cutback.core.task_outcomes import NativeTaskCostEvidence, TaskOutcomeInput, task_economics
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, Project, StoredEvent
from cutback.server.economics_db import (
    ImprovementObservation,
    TaskCostSnapshot,
    TaskRecord,
    TaskResult,
)
from cutback.server.repository import canonical_attempt
from cutback.server.trace_db import ExecutionSpan, ExecutionTrace
from cutback.server.traces import fingerprint, graph_issues

TASK_LIMIT = 100000
RESULT_LIMIT = 200000
GRAPH_WINDOW_LIMIT = 100000


def write_outcomes(session, project, environment, items, actor):
    lock_lineage(session, project)
    if not session.get(Project, project):
        raise HTTPException(404, "PROJECT_NOT_FOUND")
    counts = {
        cls: session.scalar(select(func.count()).select_from(cls).where(cls.project_id == project))
        for cls in (TaskRecord, TaskResult)
    }
    accepted = duplicates = 0
    receipts = []
    # Keep producer order: a bounded batch may contain successive CAS revisions.
    for item in items:
        record = item.model_dump(mode="json")
        digest = fingerprint(record)
        existing = session.scalar(
            select(TaskResult).where(
                TaskResult.project_id == project,
                TaskResult.environment_id == environment,
                TaskResult.result_id == item.result_id,
            )
        )
        if existing:
            if existing.fingerprint != digest:
                raise HTTPException(409, "TASK_RESULT_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
            duplicates += 1
            receipts.append({"result_id": item.result_id, "revision": existing.revision})
            continue
        if counts[TaskResult] >= RESULT_LIMIT:
            raise HTTPException(409, "TASK_RESULT_PROJECT_QUOTA")
        task = session.scalar(
            select(TaskRecord).where(
                TaskRecord.project_id == project,
                TaskRecord.environment_id == environment,
                TaskRecord.task_id == item.task_id,
            )
        )
        if task is None:
            if item.expected_previous_revision:
                raise HTTPException(409, "TASK_REVISION_CONFLICT_BATCH_ROLLED_BACK")
            if counts[TaskRecord] >= TASK_LIMIT:
                raise HTTPException(409, "TASK_PROJECT_QUOTA")
            allocated = session.scalar(
                select(TaskRecord.id).where(
                    TaskRecord.project_id == project,
                    TaskRecord.environment_id == environment,
                    TaskRecord.trace_id == item.trace_id,
                )
            )
            if allocated:
                raise HTTPException(409, "TRACE_ALREADY_ALLOCATED_TO_TASK")
            task = TaskRecord(
                id=uuid4(),
                project_id=project,
                environment_id=environment,
                task_id=item.task_id,
                trace_id=item.trace_id,
                workflow=item.workflow,
                service=item.service,
                release=item.release,
                occurred_at=item.occurred_at,
                current_revision=0,
            )
            session.add(task)
            session.flush()
            counts[TaskRecord] += 1
        if task.current_revision != item.expected_previous_revision:
            raise HTTPException(409, "TASK_REVISION_CONFLICT_BATCH_ROLLED_BACK")
        if any(
            getattr(task, field) != getattr(item, field)
            for field in ("trace_id", "workflow", "service", "release", "occurred_at")
        ):
            raise HTTPException(409, "TASK_TARGET_IS_IMMUTABLE")
        task.current_revision += 1
        session.add(
            TaskResult(
                project_id=project,
                environment_id=environment,
                task_record_id=task.id,
                result_id=item.result_id,
                revision=task.current_revision,
                fingerprint=digest,
                metadata_record=record,
                actor=actor,
            )
        )
        session.flush()
        counts[TaskResult] += 1
        accepted += 1
        receipts.append({"result_id": item.result_id, "revision": task.current_revision})
    session.add(
        AuditEvent(
            project_id=project,
            actor=actor,
            action="task_outcomes_recorded",
            details={
                "environment_id": str(environment),
                "accepted": accepted,
                "duplicates": duplicates,
            },
        )
    )
    from cutback.server.billing import meter_ingestion

    meter_ingestion(session, project, accepted)
    return {"accepted": accepted, "duplicates": duplicates, "receipts": receipts}


def latest_results(project, environment, as_of):
    """Stable declaration watermark; retained telemetry is joined at report time."""
    ranked = (
        select(
            TaskResult.id,
            func.row_number()
            .over(partition_by=TaskResult.task_record_id, order_by=TaskResult.revision.desc())
            .label("position"),
        )
        .where(
            TaskResult.project_id == project,
            TaskResult.environment_id == environment,
            TaskResult.received_at <= as_of,
        )
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.position == 1)


def calculate_rows(session, project, environment, rows):
    if not rows:
        return []
    trace_ids = [task.trace_id for task, _ in rows]
    app = StoredEvent.event["traffic_kind"].as_string() == "application"
    trace_key = StoredEvent.event["trace_id"].as_string()
    aggregates = session.execute(
        select(
            trace_key.label("trace_id"),
            func.count().filter(app).label("observed"),
            func.count(StoredEvent.cost_usd).filter(app).label("priced"),
            func.sum(StoredEvent.cost_usd).filter(app).label("subtotal"),
            func.count().filter(~func.coalesce(app, False)).label("excluded"),
        )
        .where(
            StoredEvent.project_id == project,
            StoredEvent.environment_id == environment,
            StoredEvent.event["trace_id"].as_string().in_(trace_ids),
            canonical_attempt(),
        )
        .group_by(trace_key)
    ).all()
    costs = {r.trace_id: r for r in aggregates}
    span_query = (
        select(
            ExecutionTrace.trace_id,
            ExecutionSpan.metadata_record,
            func.row_number()
            .over(partition_by=ExecutionTrace.id, order_by=ExecutionSpan.span_id)
            .label("position"),
        )
        .join(ExecutionSpan, ExecutionSpan.trace_record_id == ExecutionTrace.id)
        .where(
            ExecutionTrace.project_id == project,
            ExecutionTrace.environment_id == environment,
            ExecutionSpan.project_id == project,
            ExecutionSpan.environment_id == environment,
            ExecutionTrace.trace_id.in_(trace_ids),
        )
        .subquery()
    )
    graph_rows = session.execute(
        select(span_query.c.trace_id, span_query.c.metadata_record)
        .where(span_query.c.position <= 1001)
        .order_by(span_query.c.trace_id, span_query.c.position)
        .limit(GRAPH_WINDOW_LIMIT + 1)
    ).all()
    graphs = defaultdict(list)
    for tid, record in graph_rows[:GRAPH_WINDOW_LIMIT]:
        graphs[tid].append(record)
    # SDK attempts retain their own span identity. A retained matching span OR
    # retained parent can link them without inventing a generic SDK span. The
    # observed native wall-clock start must fit that span. Native duration is
    # monotonic elapsed time: adding it to UTC would invent a wall-clock end and
    # falsely report clock skew on platforms with differing clock resolution.
    native_start = func.extract("epoch", StoredEvent.occurred_at) * 1000000000
    joined_span = exists(
        select(ExecutionSpan.id)
        .join(ExecutionTrace, ExecutionTrace.id == ExecutionSpan.trace_record_id)
        .where(
            ExecutionSpan.project_id == StoredEvent.project_id,
            ExecutionSpan.environment_id == StoredEvent.environment_id,
            ExecutionTrace.project_id == StoredEvent.project_id,
            ExecutionTrace.environment_id == StoredEvent.environment_id,
            ExecutionTrace.trace_id == StoredEvent.event["trace_id"].as_string(),
            or_(
                ExecutionSpan.span_id == StoredEvent.event["span_id"].as_string(),
                ExecutionSpan.span_id == StoredEvent.event["parent_span_id"].as_string(),
            ),
            ExecutionSpan.started_ns <= native_start,
            cast(ExecutionSpan.metadata_record["ended_ns"].as_string(), BigInteger) >= native_start,
        )
    )
    unmatched = set(
        session.scalars(
            select(StoredEvent.event["trace_id"].as_string())
            .where(
                StoredEvent.project_id == project,
                StoredEvent.environment_id == environment,
                StoredEvent.event["trace_id"].as_string().in_(trace_ids),
                canonical_attempt(),
                app,
                ~joined_span,
            )
            .distinct()
        )
    )
    linked_attempt = exists(
        select(StoredEvent.id).where(
            StoredEvent.project_id == project,
            StoredEvent.environment_id == environment,
            StoredEvent.event["trace_id"].as_string() == span_query.c.trace_id,
            or_(
                StoredEvent.event["span_id"].as_string()
                == span_query.c.metadata_record["span_id"].as_string(),
                StoredEvent.event["parent_span_id"].as_string()
                == span_query.c.metadata_record["span_id"].as_string(),
            ),
            canonical_attempt(),
            app,
        )
    )
    missing_attempts = set(
        session.scalars(
            select(span_query.c.trace_id)
            .where(
                span_query.c.position <= 1000,
                span_query.c.metadata_record["kind"].as_string() == "llm",
                ~linked_attempt,
            )
            .distinct()
        )
    )
    output = []
    for task, result in rows:
        graph = graphs[task.trace_id]
        flags, issues = graph_issues(
            graph[:1000], len(graph) > 1000 or len(graph_rows) > GRAPH_WINDOW_LIMIT
        )
        if task.trace_id in unmatched:
            flags.append("native_attempt_outside_retained_graph")
        if task.trace_id in missing_attempts:
            flags.append("llm_span_without_application_attempt_receipt")
        if any(s["ended_ns"] is None for s in graph):
            flags.append("unfinished_span")
        if any(
            (s["workflow"], s["service"], s["release"])
            != (task.workflow, task.service, task.release)
            for s in graph
            if s["parent_span_id"] is None
        ):
            flags.append("task_root_identity_mismatch")
        cost = costs.get(task.trace_id)
        if cost and max(cost.observed, cost.excluded) > 1000000:
            raise HTTPException(413, "TASK_ATTEMPT_COUNT_BOUND")
        evidence = NativeTaskCostEvidence(
            observed_attempts=cost.observed if cost else 0,
            priced_attempts=cost.priced if cost else 0,
            known_modeled_subtotal_usd=cost.subtotal if cost and cost.priced else None,
            excluded_non_application_attempts=cost.excluded if cost else 0,
            trace_retained=bool(graph),
            trace_integrity_complete=bool(
                graph and flags == ["capture_coverage_unknown"] and not any(issues.values())
            ),
        )
        output.append(
            {
                "id": task.id,
                "result_record_id": result.id,
                "revision": result.revision,
                "declaration": result.metadata_record,
                "trace_integrity": flags,
                "span_issue_count": sum(bool(v) for v in issues.values()),
                "economics": task_economics(
                    TaskOutcomeInput.model_validate(result.metadata_record), evidence
                ),
            }
        )
    return output


def retain_task_outcomes(session, cutoff, selected):
    """Task lifetime follows raw metadata retention; prune old revisions only."""
    from cutback.server.outcome_watch_db import OutcomeWatch, OutcomeWatchWindow

    now = datetime.now(UTC)
    session.execute(
        update(OutcomeWatch)
        .where(
            selected(OutcomeWatch.project_id),
            OutcomeWatch.enabled.is_(True),
            cast(
                OutcomeWatch.specification["policy"]["expires_at"].as_string(),
                DateTime(timezone=True),
            )
            <= now,
        )
        .values(enabled=False, status="EXPIRED", updated_at=now)
    )
    session.execute(
        delete(OutcomeWatchWindow).where(
            selected(OutcomeWatchWindow.project_id), OutcomeWatchWindow.created_at < cutoff
        )
    )
    session.execute(
        delete(OutcomeWatch).where(
            selected(OutcomeWatch.project_id),
            OutcomeWatch.enabled.is_(False),
            OutcomeWatch.updated_at < cutoff,
        )
    )
    session.execute(
        delete(TaskRecord).where(TaskRecord.occurred_at < cutoff, selected(TaskRecord.project_id))
    )
    session.execute(
        delete(ImprovementObservation).where(
            ImprovementObservation.created_at < cutoff,
            selected(ImprovementObservation.project_id),
        )
    )
    referenced = exists(
        select(ImprovementObservation.id).where(
            or_(
                ImprovementObservation.baseline_snapshot_id == TaskCostSnapshot.id,
                ImprovementObservation.candidate_snapshot_id == TaskCostSnapshot.id,
            ),
        )
    )
    watch_reference = exists(
        select(OutcomeWatchWindow.id).where(
            OutcomeWatchWindow.project_id == TaskCostSnapshot.project_id,
            OutcomeWatchWindow.candidate_snapshot_id == TaskCostSnapshot.id,
        )
    )
    approved_baseline = exists(
        select(OutcomeWatch.id).where(
            OutcomeWatch.project_id == TaskCostSnapshot.project_id,
            OutcomeWatch.environment_id == TaskCostSnapshot.environment_id,
            OutcomeWatch.enabled.is_(True),
            OutcomeWatch.specification["policy"]["baseline_snapshot_id"].as_string()
            == cast(TaskCostSnapshot.id, String),
        )
    )
    from cutback.server.campaign_db import CampaignEconomicReference
    from cutback.server.routing_program_db import RoutingProgramRecord

    program_reference = exists(
        select(RoutingProgramRecord.id).where(
            RoutingProgramRecord.snapshot_id == TaskCostSnapshot.id,
            RoutingProgramRecord.project_id == TaskCostSnapshot.project_id,
        )
    )
    campaign_reference = exists(
        select(CampaignEconomicReference.campaign_id).where(
            CampaignEconomicReference.snapshot_id == TaskCostSnapshot.id,
            CampaignEconomicReference.project_id == TaskCostSnapshot.project_id,
        )
    )
    session.execute(
        delete(TaskCostSnapshot).where(
            TaskCostSnapshot.created_at < cutoff,
            selected(TaskCostSnapshot.project_id),
            ~referenced,
            ~watch_reference,
            ~approved_baseline,
            ~program_reference,
            ~campaign_reference,
        )
    )
    current = exists(
        select(TaskRecord.id).where(
            TaskRecord.id == TaskResult.task_record_id,
            TaskRecord.current_revision == TaskResult.revision,
        )
    )
    session.execute(
        delete(TaskResult).where(
            TaskResult.received_at < cutoff, selected(TaskResult.project_id), ~current
        )
    )
