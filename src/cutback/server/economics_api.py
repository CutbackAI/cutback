"""Scoped content-free task outcomes and explicit, bounded cost coverage."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import AwareDatetime, Field, StrictInt
from sqlalchemy import func, select

from cutback.core.models import Identifier, StrictModel
from cutback.core.task_outcomes import (
    TaskCostReport,
    TaskEconomicsSummary,
    TaskOutcomeBatch,
    TaskOutcomeInput,
    summarize_task_economics,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, Environment
from cutback.server.economics_db import TaskCostSnapshot, TaskRecord, TaskResult
from cutback.server.task_economics import calculate_rows, latest_results, write_outcomes
from cutback.server.traces import fingerprint


class TaskRevisionReceipt(StrictModel):
    result_id: UUID
    revision: int


class TaskIngestReceipt(StrictModel):
    accepted: int
    duplicates: int
    receipts: list[TaskRevisionReceipt]


class TaskReceipt(StrictModel):
    id: UUID
    result_record_id: UUID
    revision: int
    declaration: TaskOutcomeInput
    trace_integrity: list[str]
    span_issue_count: int
    economics: TaskCostReport


class TaskPage(StrictModel):
    items: list[TaskReceipt]
    next_after: UUID | None
    declarations_as_of: datetime
    calculated_at: datetime
    evidence_scope: str = "retained_evidence_at_calculation_time_not_frozen_telemetry"


class TaskWindow(StrictModel):
    summary: TaskEconomicsSummary
    items: list[TaskReceipt]
    environment_id: UUID
    start: datetime
    end: datetime
    declarations_as_of: datetime
    calculated_at: datetime
    evidence_scope: str = "retained_evidence_at_calculation_time_not_frozen_telemetry"


class TaskSnapshotInput(StrictModel):
    snapshot_id: UUID
    start: AwareDatetime
    end: AwareDatetime
    workflow: Identifier | None = None
    release: Identifier | None = None
    expected_tasks: StrictInt | None = Field(default=None, ge=0, le=1000000)
    as_of: AwareDatetime | None = None


class TaskSnapshotReceipt(StrictModel):
    id: UUID
    environment_id: UUID
    evidence_sha256: str
    report: TaskWindow
    created_at: datetime
    evidence_scope: str = "frozen_retained_evidence_not_independent_or_causal_verification"


class TaskSnapshotPage(StrictModel):
    items: list["TaskSnapshotBrief"]
    next_after: UUID | None


class TaskSnapshotBrief(StrictModel):
    id: UUID
    environment_id: UUID
    evidence_sha256: str
    summary: TaskEconomicsSummary
    workflow: str | None
    release: str | None
    start: datetime
    end: datetime
    created_at: datetime


def read_window(session, project, environment, start, end, at, workflow, release, expected_tasks):
    if not at - timedelta(days=90) <= start < end <= at or end - start > timedelta(days=30):
        raise HTTPException(400, "TASK_COST_WINDOW_REQUIRES_AT_MOST_30_DAYS_WITHIN_RETENTION")
    query = (
        select(TaskRecord, TaskResult)
        .join(TaskResult, TaskResult.task_record_id == TaskRecord.id)
        .where(
            TaskResult.id.in_(latest_results(project, environment, at)),
            TaskRecord.occurred_at >= start,
            TaskRecord.occurred_at < end,
        )
    )
    if workflow is not None:
        query = query.where(TaskRecord.workflow == workflow)
    if release is not None:
        query = query.where(TaskRecord.release == release)
    rows = session.execute(query.order_by(TaskRecord.id).limit(10001)).all()
    if len(rows) > 10000:
        raise HTTPException(413, "TASK_COST_WINDOW_EXCEEDS_10000_TASKS_NARROW_FILTERS")
    items = calculate_rows(session, project, environment, rows)
    return TaskWindow(
        summary=TaskEconomicsSummary.model_validate(
            summarize_task_economics(
                [item["economics"] for item in items],
                expected_tasks=expected_tasks,
            )
        ),
        items=items,
        environment_id=environment,
        start=start,
        end=end,
        declarations_as_of=at,
        calculated_at=datetime.now(UTC),
    )


def snapshot_receipt(row):
    return TaskSnapshotReceipt(
        id=row.id,
        environment_id=row.environment_id,
        evidence_sha256=row.evidence_sha256,
        report=TaskWindow.model_validate(row.report),
        created_at=row.created_at,
    )


def install_economics(app, sessions, authorize, scope):
    def environment(session, project, identifier):
        row = session.get(Environment, identifier)
        if not row or row.project_id != project:
            raise HTTPException(404, "ENVIRONMENT_NOT_FOUND")
        return row

    def watermark(as_of):
        now = datetime.now(UTC)
        if as_of and not now - timedelta(days=90) <= as_of <= now:
            raise HTTPException(400, "TASK_DECLARATION_WATERMARK_OUTSIDE_WINDOW")
        return as_of or now

    @app.post("/v1/ingest/task-outcomes", response_model=TaskIngestReceipt)
    def ingest(body: TaskOutcomeBatch, auth=Depends(authorize)):
        project = auth.get("project_id")
        if project is None or auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        scope(auth, project, ("ingest",))
        with sessions.begin() as session:
            env = environment(session, project, auth["environment_id"])
            if any(item.environment != env.name for item in body.items):
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
            return write_outcomes(session, project, env.id, body.items, auth["actor"])

    @app.post("/v1/projects/{project_id}/task-outcomes", response_model=TaskIngestReceipt)
    def record(
        project_id: UUID,
        environment_id: UUID,
        body: TaskOutcomeBatch,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        with sessions.begin() as session:
            env = environment(session, project_id, environment_id)
            if any(item.environment != env.name for item in body.items):
                raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
            return write_outcomes(session, project_id, env.id, body.items, auth["actor"])

    @app.get("/v1/projects/{project_id}/task-outcomes", response_model=TaskPage)
    def tasks(
        project_id: UUID,
        environment_id: UUID,
        as_of: AwareDatetime | None = None,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        at = watermark(as_of)
        with sessions() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            environment(session, project_id, environment_id)
            # UUID cursor is scoped by the query; it contains no external key.
            query = (
                select(TaskRecord, TaskResult)
                .join(TaskResult, TaskResult.task_record_id == TaskRecord.id)
                .where(TaskResult.id.in_(latest_results(project_id, environment_id, at)))
            )
            if after:
                query = query.where(TaskRecord.id > after)
            rows = session.execute(query.order_by(TaskRecord.id).limit(limit + 1)).all()
            return TaskPage(
                items=calculate_rows(session, project_id, environment_id, rows[:limit]),
                next_after=rows[limit - 1][0].id if len(rows) > limit else None,
                declarations_as_of=at,
                calculated_at=datetime.now(UTC),
            )

    @app.get("/v1/projects/{project_id}/task-economics", response_model=TaskWindow)
    def economics(
        project_id: UUID,
        environment_id: UUID,
        start: AwareDatetime,
        end: AwareDatetime,
        workflow: str | None = Query(
            None, min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.:-]+$"
        ),
        release: str | None = Query(
            None, min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.:-]+$"
        ),
        expected_tasks: int | None = Query(None, ge=0, le=1000000),
        as_of: AwareDatetime | None = None,
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        at = watermark(as_of)
        with sessions() as session:
            session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            environment(session, project_id, environment_id)
            return read_window(
                session,
                project_id,
                environment_id,
                start,
                end,
                at,
                workflow,
                release,
                expected_tasks,
            )

    @app.post("/v1/projects/{project_id}/task-cost-snapshots", response_model=TaskSnapshotReceipt)
    def freeze(
        project_id: UUID,
        environment_id: UUID,
        body: TaskSnapshotInput,
        auth=Depends(authorize),
    ):
        scope(auth, project_id, ("analyst",))
        specification = {"environment_id": str(environment_id), **body.model_dump(mode="json")}
        digest = fingerprint(specification)
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            environment(session, project_id, environment_id)
            existing = session.scalar(
                select(TaskCostSnapshot).where(
                    TaskCostSnapshot.project_id == project_id,
                    TaskCostSnapshot.snapshot_id == body.snapshot_id,
                )
            )
            if existing:
                if existing.request_sha256 != digest:
                    raise HTTPException(409, "TASK_SNAPSHOT_REQUEST_CONFLICT")
                return snapshot_receipt(existing)
            if (
                session.scalar(
                    select(func.count())
                    .select_from(TaskCostSnapshot)
                    .where(
                        TaskCostSnapshot.project_id == project_id,
                    )
                )
                >= 1000
            ):
                raise HTTPException(409, "TASK_SNAPSHOT_PROJECT_QUOTA")
            # Serialize idempotent mutations under the project lock, but read
            # telemetry from one independent repeatable-read snapshot. Waiting
            # for that lock must not freeze a stale mutation-identity lookup.
            with sessions() as reader:
                reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                report = read_window(
                    reader,
                    project_id,
                    environment_id,
                    body.start,
                    body.end,
                    watermark(body.as_of),
                    body.workflow,
                    body.release,
                    body.expected_tasks,
                ).model_dump(mode="json")
            if len(report["items"]) > 1000 or len(json.dumps(report).encode()) > 4000000:
                raise HTTPException(413, "TASK_SNAPSHOT_TOO_LARGE_NARROW_FILTERS")
            report["evidence_scope"] = "frozen_retained_evidence_at_calculation_time"
            row = TaskCostSnapshot(
                project_id=project_id,
                environment_id=environment_id,
                snapshot_id=body.snapshot_id,
                request_sha256=digest,
                evidence_sha256=fingerprint(report),
                specification=specification,
                report=report,
                actor=auth["actor"],
            )
            session.add(row)
            session.flush()
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="task_cost_snapshot_frozen",
                    details={"snapshot_id": str(row.id), "evidence_sha256": row.evidence_sha256},
                )
            )
            return snapshot_receipt(row)

    @app.get("/v1/projects/{project_id}/task-cost-snapshots", response_model=TaskSnapshotPage)
    def snapshots(
        project_id: UUID,
        environment_id: UUID,
        after: UUID | None = None,
        limit: int = Query(10, ge=1, le=20),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            environment(session, project_id, environment_id)
            query = select(TaskCostSnapshot).where(
                TaskCostSnapshot.project_id == project_id,
                TaskCostSnapshot.environment_id == environment_id,
            )
            if after:
                query = query.where(TaskCostSnapshot.id > after)
            # Project only bounded metadata; never load twenty multi-megabyte reports.
            rows = session.execute(
                query.with_only_columns(
                    TaskCostSnapshot.id,
                    TaskCostSnapshot.environment_id,
                    TaskCostSnapshot.evidence_sha256,
                    TaskCostSnapshot.report["summary"].label("summary"),
                    TaskCostSnapshot.specification["workflow"].as_string().label("workflow"),
                    TaskCostSnapshot.specification["release"].as_string().label("release"),
                    TaskCostSnapshot.report["start"].as_string().label("start"),
                    TaskCostSnapshot.report["end"].as_string().label("end"),
                    TaskCostSnapshot.created_at,
                )
                .order_by(TaskCostSnapshot.id)
                .limit(limit + 1)
            ).all()
            return TaskSnapshotPage(
                items=[
                    TaskSnapshotBrief.model_validate(dict(row._mapping)) for row in rows[:limit]
                ],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get(
        "/v1/projects/{project_id}/task-cost-snapshots/{snapshot_id}",
        response_model=TaskSnapshotReceipt,
    )
    def snapshot(project_id: UUID, snapshot_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(TaskCostSnapshot, snapshot_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "TASK_SNAPSHOT_NOT_FOUND")
            return snapshot_receipt(row)
