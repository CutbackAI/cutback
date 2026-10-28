"""Bounded, tenant-scoped workflow investigation. No payload or inferred success."""

import hashlib
import json
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query
from pydantic import AwareDatetime, StringConstraints
from sqlalchemy import delete, exists, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert

from cutback.core.models import Identifier, StrictModel
from cutback.core.traces import (
    EvaluationBatch,
    EvaluationPage,
    SessionPage,
    SpanBatch,
    TraceComparison,
    TraceDetail,
    TracePage,
    WorkflowSpan,
)
from cutback.server.db import Environment, StoredEvent
from cutback.server.trace_db import (
    ExecutionSession,
    ExecutionSpan,
    ExecutionTrace,
    ExternalEvaluation,
    SessionTrace,
    TraceContent,
)

PROFILE = "cutback.workflow.v1"


class EnvironmentInput(StrictModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.-]{1,100}$")]


def fingerprint(record):
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def page_before(session, query, cls, project, environment, before):
    if before:
        cursor = session.scalar(
            select(cls).where(
                cls.id == before, cls.project_id == project, cls.environment_id == environment
            )
        )
        if not cursor:
            raise HTTPException(400, "CURSOR_EXPIRED_OR_OUT_OF_SCOPE")
        query = query.where(tuple_(cls.received_at, cls.id) < tuple_(cursor.received_at, cursor.id))
    return query.order_by(cls.received_at.desc(), cls.id.desc())


def identity(session, cls, fields, constraint):
    session.execute(
        insert(cls).values(id=uuid4(), **fields).on_conflict_do_nothing(constraint=constraint)
    )
    return session.scalar(select(cls.id).where(*(getattr(cls, k) == v for k, v in fields.items())))


def scoped_identities(session, cls, project, environment, name, identifiers, constraint):
    keys = sorted(set(identifiers))
    if not keys:
        return {}
    session.execute(
        insert(cls)
        .values(
            [
                {"id": uuid4(), "project_id": project, "environment_id": environment, name: key}
                for key in keys
            ]
        )
        .on_conflict_do_nothing(constraint=constraint)
    )
    field = getattr(cls, name)
    return dict(
        session.execute(
            select(field, cls.id).where(
                cls.project_id == project, cls.environment_id == environment, field.in_(keys)
            )
        ).all()
    )


def ingest_spans(session, project, environment, body):
    from cutback.server.dataset_lineage import lock_lineage
    from cutback.server.monitoring_db import OnlineIntake, OnlineRule

    # This lock also serializes policy cutover, intake and conflicting identities.
    # No evaluator fan-out or model call belongs in the ingestion request path.
    lock_lineage(session, project)
    online_enabled = (
        session.scalar(
            select(OnlineRule.id)
            .where(
                OnlineRule.project_id == project,
                OnlineRule.environment_id == environment,
                OnlineRule.enabled.is_(True),
            )
            .limit(1)
        )
        is not None
    )
    requested: dict[tuple[str, str], tuple[dict, str, WorkflowSpan]] = {}
    for span in body.spans:
        record = span.model_dump(mode="json")
        digest = fingerprint(record)
        key = (span.trace_id, span.span_id)
        if key in requested and requested[key][1] != digest:
            raise HTTPException(409, "SPAN_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
        requested.setdefault(key, (record, digest, span))
    traces = scoped_identities(
        session,
        ExecutionTrace,
        project,
        environment,
        "trace_id",
        [key[0] for key in requested],
        "uq_execution_trace",
    )
    records = [
        {
            "id": uuid4(),
            "project_id": project,
            "environment_id": environment,
            "trace_record_id": traces[trace],
            "span_id": identifier,
            "started_ns": span.started_ns,
            "fingerprint": digest,
            "metadata_record": record,
        }
        for (trace, identifier), (record, digest, span) in sorted(requested.items())
    ]
    added = session.scalars(
        insert(ExecutionSpan)
        .values(records)
        .on_conflict_do_nothing(constraint="uq_execution_span")
        .returning(ExecutionSpan.id)
    ).all()
    expected = {
        (traces[trace], identifier): value[1] for (trace, identifier), value in requested.items()
    }
    # Check both existing and inserted rows after the upsert. A conflicting
    # concurrent/direct writer cannot hide behind ON CONFLICT DO NOTHING.
    retained = {
        (row.trace_record_id, row.span_id): row.fingerprint
        for row in session.execute(
            select(
                ExecutionSpan.trace_record_id, ExecutionSpan.span_id, ExecutionSpan.fingerprint
            ).where(
                ExecutionSpan.project_id == project,
                ExecutionSpan.environment_id == environment,
                tuple_(ExecutionSpan.trace_record_id, ExecutionSpan.span_id).in_(list(expected)),
            )
        )
    }
    if retained != expected:
        raise HTTPException(409, "SPAN_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
    if online_enabled and added:
        session.execute(
            insert(OnlineIntake)
            .values(
                [
                    {
                        "id": uuid4(),
                        "project_id": project,
                        "environment_id": environment,
                        "span_record_id": identifier,
                    }
                    for identifier in added
                ]
            )
            .on_conflict_do_nothing(constraint="uq_online_intake_span")
        )
    sessions = scoped_identities(
        session,
        ExecutionSession,
        project,
        environment,
        "session_id",
        [value[2].session_id for value in requested.values() if value[2].session_id],
        "uq_execution_session",
    )
    links = sorted(
        {
            (sessions[value[2].session_id], traces[trace])
            for (trace, _), value in requested.items()
            if value[2].session_id
        }
    )
    if links:
        session.execute(
            insert(SessionTrace)
            .values(
                [
                    {
                        "id": uuid4(),
                        "project_id": project,
                        "session_record_id": sid,
                        "trace_record_id": trace,
                    }
                    for sid, trace in links
                ]
            )
            .on_conflict_do_nothing(constraint="uq_session_trace")
        )
    from cutback.server.billing import meter_ingestion

    accepted = len(added)
    meter_ingestion(session, project, accepted)
    return {
        "profile": PROFILE,
        "accepted": accepted,
        "duplicates": len(body.spans) - accepted,
        "conflicts": 0,
    }


def graph_issues(spans, truncated):
    by_id = {s["span_id"]: s for s in spans}
    issues: dict[str, list[str]] = {s: [] for s in by_id}
    finished: set[str] = set()
    for identifier, span in by_id.items():
        parent = by_id.get(span["parent_span_id"])
        if span["parent_span_id"] and parent is None:
            issues[identifier].append("parent_not_in_retained_page")
        if parent and (
            span["started_ns"] < parent["started_ns"]
            or (parent["ended_ns"] and span["ended_ns"] and span["ended_ns"] > parent["ended_ns"])
        ):
            issues[identifier].append("outside_parent_interval")
        if identifier in finished:
            continue
        path: list[str] = []
        positions: dict[str, int] = {}
        current = identifier
        while current in by_id and current not in finished:
            if current in positions:
                for node in path[positions[current] :]:
                    issues[node].append("parent_cycle")
                break
            positions[current] = len(path)
            path.append(current)
            current = by_id[current]["parent_span_id"]
        finished.update(path)
    flags = ["capture_coverage_unknown"]
    if not any(s["parent_span_id"] is None for s in spans):
        flags.append("root_not_observed")
    if sum(s["parent_span_id"] is None for s in spans) > 1:
        flags.append("multiple_roots")
    if truncated:
        flags.append("span_page_truncated")
    return flags, issues


def native_evidence(session, project, environment, trace_id, span_ids):
    # Never sum an OTLP mirror and a native SDK observation for the same span.
    rows = (
        session.scalars(
            select(StoredEvent)
            .where(
                StoredEvent.project_id == project,
                StoredEvent.environment_id == environment,
                StoredEvent.event["trace_id"].as_string() == trace_id,
                StoredEvent.event["span_id"].as_string().in_(span_ids),
            )
            .order_by(StoredEvent.id)
            .limit(2001)
        ).all()
        if span_ids
        else []
    )
    groups: dict[str, list] = {}
    for row in rows[:2000]:
        groups.setdefault(row.event["span_id"], []).append(row)
    result = {}
    for sid in span_ids:
        matches = groups.get(sid, [])
        native = [
            r for r in matches if r.event.get("sampling_policy_id") != "external-otel-spans-v1"
        ]
        candidates = native or matches
        canonical = candidates[0] if len(candidates) == 1 and len(rows) <= 2000 else None
        result[sid] = {
            "status": "canonical_native"
            if canonical and native
            else "canonical_external"
            if canonical
            else "ambiguous"
            if matches
            else "not_observed",
            "observations": len(matches),
            "observation_page_truncated": len(rows) > 2000,
            "event_id": str(canonical.event_id) if canonical else None,
            "cost_usd": str(canonical.cost_usd)
            if canonical and canonical.cost_usd is not None
            else None,
            "cost_status": canonical.cost_status if canonical else "unknown",
            "replay_supported": False,
        }
    return result


def trace_detail(session, row, limit):
    retained = session.scalars(
        select(ExecutionSpan)
        .where(ExecutionSpan.trace_record_id == row.id)
        .order_by(ExecutionSpan.started_ns, ExecutionSpan.span_id)
        .limit(limit + 1)
    ).all()
    truncated = len(retained) > limit
    spans = [r.metadata_record for r in retained[:limit]]
    content_ids = dict(
        session.execute(
            select(TraceContent.span_id, TraceContent.id).where(
                TraceContent.trace_record_id == row.id,
                TraceContent.span_id.in_([s["span_id"] for s in spans]),
            )
        ).all()
    )
    flags, issues = graph_issues(spans, truncated)
    native = native_evidence(
        session,
        row.project_id,
        row.environment_id,
        row.trace_id,
        [s["span_id"] for s in spans if s["kind"] == "llm"],
    )
    evaluations = session.scalars(
        select(ExternalEvaluation)
        .where(
            ExternalEvaluation.project_id == row.project_id,
            ExternalEvaluation.environment_id == row.environment_id,
            ExternalEvaluation.trace_id == row.trace_id,
        )
        .order_by(ExternalEvaluation.id)
        .limit(201)
    ).all()
    known = [Decimal(v["cost_usd"]) for v in native.values() if v["cost_usd"] is not None]
    return {
        "profile": PROFILE,
        "trace_id": row.trace_id,
        "environment_id": str(row.environment_id),
        "spans": [
            {
                **s,
                "started_ns": str(s["started_ns"]),
                "ended_ns": str(s["ended_ns"]) if s["ended_ns"] else None,
                "issues": issues[s["span_id"]],
                "native": native.get(s["span_id"]),
                "content_available": s["span_id"] in content_ids,
                "content_id": content_ids.get(s["span_id"]),
            }
            for s in spans
        ],
        "integrity": flags,
        "truncated": truncated,
        "returned_spans": len(spans),
        "observed_execution_errors": sum(s["status"] == "error" for s in spans),
        "business_success": "unknown",
        "full_trace_cost_usd": None,
        "page_known_llm_cost_usd": str(sum(known, Decimal(0))) if known else None,
        "cost_coverage": "partial_observations_only",
        "replay_supported": False,
        "evaluations": [evaluation_record(r, spans) for r in evaluations[:200]],
        "evaluations_truncated": len(evaluations) > 200,
    }


def evaluation_record(row, spans=None):
    record = row.metadata_record
    joined = (
        None
        if spans is None
        else not record["span_id"] or any(s["span_id"] == record["span_id"] for s in spans)
    )
    evidence = (
        row.source_origin
        if row.source_origin in {"cutback_model_judge", "cutback_technical_contract"}
        else "externally_asserted"
    )
    return {**record, "evidence": evidence, "target_in_retained_page": joined}


def prune_traces(session, cutoff, selected):
    """Same raw retention boundary; never invent a trace aggregate after deleting spans."""
    ns = int(cutoff.timestamp() * 1_000_000_000)
    session.execute(
        delete(ExecutionSpan).where(
            ExecutionSpan.started_ns < ns, selected(ExecutionSpan.project_id)
        )
    )
    session.execute(
        delete(ExecutionTrace).where(
            selected(ExecutionTrace.project_id),
            ~exists(
                select(ExecutionSpan.id).where(ExecutionSpan.trace_record_id == ExecutionTrace.id)
            ),
        )
    )
    session.execute(
        delete(ExecutionSession).where(
            selected(ExecutionSession.project_id),
            ~exists(
                select(SessionTrace.id).where(SessionTrace.session_record_id == ExecutionSession.id)
            ),
        )
    )
    session.execute(
        delete(ExternalEvaluation).where(
            ExternalEvaluation.received_at < cutoff, selected(ExternalEvaluation.project_id)
        )
    )


def install_traces(app, sessions, authorize, scope):
    @app.get("/v1/projects/{project_id}/environments")
    def environments(
        project_id: UUID,
        before: UUID | None = None,
        limit: int = Query(100, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        query = select(Environment).where(Environment.project_id == project_id)
        if before:
            query = query.where(Environment.id < before)
        with sessions() as session:
            rows = session.scalars(query.order_by(Environment.id.desc()).limit(limit + 1)).all()
            return {
                "items": [{"id": str(r.id), "name": r.name} for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.post("/v1/projects/{project_id}/environments", status_code=201)
    def environment_create(project_id: UUID, body: EnvironmentInput, auth=Depends(authorize)):
        from cutback.server.db import AuditEvent, Project

        scope(auth, project_id, ("admin",))
        name = body.name
        with sessions.begin() as session:
            if not session.get(Project, project_id):
                raise HTTPException(404, "PROJECT_NOT_FOUND")
            identifier = identity(
                session,
                Environment,
                {"project_id": project_id, "name": name},
                "environments_project_id_name_key",
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="environment_configured",
                    details={"environment_id": str(identifier)},
                )
            )
            return {"id": str(identifier), "name": name}

    def ingest_scope(auth, session):
        if auth["role"] != "ingest":
            raise HTTPException(403, "INGEST_SCOPE_REQUIRED")
        project = UUID(str(auth["project_id"]))
        scope(auth, project, ("ingest",))
        env = session.get(Environment, auth["environment_id"])
        if not env or env.project_id != project:
            raise HTTPException(403, "ENVIRONMENT_SCOPE_DENIED")
        return project, env

    @app.post("/v1/ingest/spans")
    def workflow_spans(body: SpanBatch, auth=Depends(authorize)):
        with sessions.begin() as session:
            project, env = ingest_scope(auth, session)
            return ingest_spans(session, project, env.id, body)

    @app.post("/v1/ingest/evaluations")
    def external_evaluations(body: EvaluationBatch, auth=Depends(authorize)):
        accepted = duplicates = 0
        with sessions.begin() as session:
            project, env = ingest_scope(auth, session)
            for result in sorted(body.results, key=lambda value: value.result_id):
                record = result.model_dump(mode="json")
                digest = fingerprint(record)
                added = session.scalar(
                    insert(ExternalEvaluation)
                    .values(
                        id=uuid4(),
                        project_id=project,
                        environment_id=env.id,
                        result_id=result.result_id,
                        source_origin="external",
                        trace_id=result.trace_id,
                        session_id=result.session_id,
                        fingerprint=digest,
                        metadata_record=record,
                    )
                    .on_conflict_do_nothing(constraint="uq_external_evaluation")
                    .returning(ExternalEvaluation.id)
                )
                if added:
                    accepted += 1
                elif (
                    session.scalar(
                        select(ExternalEvaluation.fingerprint).where(
                            ExternalEvaluation.project_id == project,
                            ExternalEvaluation.environment_id == env.id,
                            ExternalEvaluation.result_id == result.result_id,
                            ExternalEvaluation.source_origin == "external",
                        )
                    )
                    != digest
                ):
                    raise HTTPException(409, "EVALUATION_IDENTITY_CONFLICT_BATCH_ROLLED_BACK")
                else:
                    duplicates += 1
            from cutback.server.billing import meter_ingestion

            meter_ingestion(session, project, accepted)
        return {
            "profile": body.profile,
            "accepted": accepted,
            "duplicates": duplicates,
            "conflicts": 0,
            "evidence": "externally_asserted",
        }

    @app.get("/v1/projects/{project_id}/traces", response_model=TracePage)
    def traces(
        project_id: UUID,
        environment_id: UUID,
        session_record_id: UUID | None = None,
        before: UUID | None = None,
        errors_only: bool = False,
        workflow: Identifier | None = None,
        service: Identifier | None = None,
        release: Identifier | None = None,
        model: Identifier | None = None,
        start: AwareDatetime | None = None,
        end: AwareDatetime | None = None,
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        if start and end and end <= start:
            raise HTTPException(422, "TRACE_WINDOW_INVALID")
        query = select(ExecutionTrace).where(
            ExecutionTrace.project_id == project_id, ExecutionTrace.environment_id == environment_id
        )
        if session_record_id:
            query = query.where(
                ExecutionTrace.id.in_(
                    select(SessionTrace.trace_record_id).where(
                        SessionTrace.session_record_id == session_record_id,
                        SessionTrace.project_id == project_id,
                    )
                )
            )
        filters = [ExecutionSpan.trace_record_id == ExecutionTrace.id]
        if start:
            filters.append(ExecutionSpan.started_ns >= int(start.timestamp() * 1_000_000_000))
        if end:
            filters.append(ExecutionSpan.started_ns < int(end.timestamp() * 1_000_000_000))
        if errors_only:
            filters.append(ExecutionSpan.metadata_record["status"].as_string() == "error")
        for field, value in (("workflow", workflow), ("service", service), ("release", release)):
            if value:
                filters.append(ExecutionSpan.metadata_record[field].as_string() == value)
        if len(filters) > 1:
            query = query.where(exists(select(ExecutionSpan.id).where(*filters)))
        if model:
            query = query.where(
                exists(
                    select(StoredEvent.id).where(
                        StoredEvent.project_id == ExecutionTrace.project_id,
                        StoredEvent.environment_id == ExecutionTrace.environment_id,
                        StoredEvent.event["trace_id"].as_string() == ExecutionTrace.trace_id,
                        StoredEvent.model_actual == model,
                    )
                )
            )
        with sessions() as session:
            query = page_before(session, query, ExecutionTrace, project_id, environment_id, before)
            rows = session.scalars(query.limit(limit + 1)).all()
            counts = {
                row.trace_record_id: (row.spans, row.errors)
                for row in session.execute(
                    select(
                        ExecutionSpan.trace_record_id,
                        func.count(ExecutionSpan.id).label("spans"),
                        func.count(ExecutionSpan.id)
                        .filter(ExecutionSpan.metadata_record["status"].as_string() == "error")
                        .label("errors"),
                    )
                    .where(
                        ExecutionSpan.project_id == project_id,
                        ExecutionSpan.environment_id == environment_id,
                        ExecutionSpan.trace_record_id.in_([row.id for row in rows[:limit]]),
                    )
                    .group_by(ExecutionSpan.trace_record_id)
                )
            }
            items = []
            for row in rows[:limit]:
                count, errors = counts.get(row.id, (0, 0))
                items.append(
                    {
                        "id": str(row.id),
                        "trace_id": row.trace_id,
                        "environment_id": str(row.environment_id),
                        "received_at": row.received_at,
                        "retained_spans": count,
                        "observed_execution_errors": errors,
                        "capture_coverage": "unknown",
                    }
                )
            return {
                "items": items,
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "order": "received_at_desc",
                "profile": PROFILE,
            }

    @app.get("/v1/projects/{project_id}/traces/{trace_record_id}", response_model=TraceDetail)
    def detail(
        project_id: UUID,
        trace_record_id: UUID,
        limit: int = Query(1000, ge=1, le=1000),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(ExecutionTrace, trace_record_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "TRACE_NOT_FOUND")
            return trace_detail(session, row, limit)

    @app.get(
        "/v1/projects/{project_id}/traces/{trace_record_id}/compare/{other_trace_id}",
        response_model=TraceComparison,
    )
    def compare(
        project_id: UUID, trace_record_id: UUID, other_trace_id: UUID, auth=Depends(authorize)
    ):
        from cutback.server.trace_comparison import align

        scope(auth, project_id)
        with sessions() as session:
            records = [
                session.get(ExecutionTrace, identifier)
                for identifier in (trace_record_id, other_trace_id)
            ]
            if any(row is None or row.project_id != project_id for row in records):
                raise HTTPException(404, "TRACE_NOT_FOUND")
            if records[0].environment_id != records[1].environment_id:
                raise HTTPException(409, "TRACE_COMPARISON_REQUIRES_SAME_ENVIRONMENT")
            left, right = [trace_detail(session, row, 500) for row in records]
            return {
                "left": left,
                "right": right,
                "rows": align(left["spans"], right["spans"]),
                "semantics": "exact_retained_operation_kind_ancestry_and_occurrence",
                "task_quality_comparison": "not_verified",
            }

    @app.get("/v1/projects/{project_id}/sessions", response_model=SessionPage)
    def session_list(
        project_id: UUID,
        environment_id: UUID,
        before: UUID | None = None,
        errors_only: bool = False,
        limit: int = Query(25, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        query = select(ExecutionSession).where(
            ExecutionSession.project_id == project_id,
            ExecutionSession.environment_id == environment_id,
        )
        if errors_only:
            query = query.where(
                exists(
                    select(SessionTrace.id)
                    .join(
                        ExecutionSpan, ExecutionSpan.trace_record_id == SessionTrace.trace_record_id
                    )
                    .where(
                        SessionTrace.session_record_id == ExecutionSession.id,
                        ExecutionSpan.metadata_record["status"].as_string() == "error",
                    )
                )
            )
        with sessions() as session:
            query = page_before(
                session, query, ExecutionSession, project_id, environment_id, before
            )
            rows = session.scalars(query.limit(limit + 1)).all()
            items = []
            for row in rows[:limit]:
                trace_ids = select(SessionTrace.trace_record_id).where(
                    SessionTrace.session_record_id == row.id
                )
                count = session.scalar(
                    select(func.count())
                    .select_from(SessionTrace)
                    .where(SessionTrace.session_record_id == row.id)
                )
                errors = session.scalar(
                    select(func.count())
                    .select_from(ExecutionSpan)
                    .where(
                        ExecutionSpan.trace_record_id.in_(trace_ids),
                        ExecutionSpan.metadata_record["status"].as_string() == "error",
                    )
                )
                items.append(
                    {
                        "id": str(row.id),
                        "session_id": row.session_id,
                        "received_at": row.received_at,
                        "retained_traces": count,
                        "observed_execution_errors": errors,
                    }
                )
            return {
                "items": items,
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "order": "received_at_desc",
            }

    @app.get(
        "/v1/projects/{project_id}/sessions/{session_record_id}/evaluations",
        response_model=EvaluationPage,
    )
    def session_evaluations(
        project_id: UUID,
        session_record_id: UUID,
        before: UUID | None = None,
        limit: int = Query(100, ge=1, le=200),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            row = session.get(ExecutionSession, session_record_id)
            if not row or row.project_id != project_id:
                raise HTTPException(404, "SESSION_NOT_FOUND")
            query = select(ExternalEvaluation).where(
                ExternalEvaluation.project_id == project_id,
                ExternalEvaluation.environment_id == row.environment_id,
                ExternalEvaluation.session_id == row.session_id,
            )
            if before:
                query = query.where(ExternalEvaluation.id < before)
            results = session.scalars(
                query.order_by(ExternalEvaluation.id.desc()).limit(limit + 1)
            ).all()
            return {
                "items": [evaluation_record(r) for r in results[:limit]],
                "next_cursor": str(results[limit - 1].id) if len(results) > limit else None,
            }

    from cutback.server.workflow_otel import install_workflow_otel

    install_workflow_otel(app, sessions, authorize, ingest_scope, ingest_spans)
    from cutback.server.trace_content import install_trace_content

    install_trace_content(app, sessions, authorize, scope)
