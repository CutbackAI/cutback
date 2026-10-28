"""Nonbillable online evaluation outside ingestion; durable sampling and leases."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from cutback.server.monitoring_contracts import OnlineRuleInput
from cutback.server.monitoring_db import OnlineEvaluation, OnlineIntake, OnlineRule
from cutback.server.trace_db import ExecutionSpan, ExecutionTrace, ExternalEvaluation


def matches(spec, span):
    return all(value is None or span.get(key) == value for key, value in spec["filter"].items())


def epoch_ns(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1000000000 + delta.microseconds * 1000


def selected_time(rule, span):
    spec = OnlineRuleInput.model_validate(rule.specification)
    occurred = datetime.fromtimestamp(span.started_ns // 1000000000, UTC) + timedelta(
        microseconds=(span.started_ns % 1000000000) // 1000
    )
    if not spec.starts_at <= occurred < spec.expires_at or not matches(
        rule.specification, span.metadata_record
    ):
        return None
    # Stable sampling across workers/retries; independently versioned rules get their
    # own deterministic sample. No user input/body is hashed into a public receipt.
    token = hashlib.sha256(rule.id.bytes + span.id.bytes).digest()
    if int.from_bytes(token[:8], "big") % 10000 >= spec.sample_basis_points:
        return None
    return occurred


def enqueue(session, rule, span, trace_id, now):
    occurred = selected_time(rule, span)
    if occurred is None:
        return False
    spec = OnlineRuleInput.model_validate(rule.specification)
    session.execute(
        insert(OnlineEvaluation)
        .values(
            id=uuid4(),
            project_id=rule.project_id,
            rule_id=rule.id,
            span_record_id=span.id,
            trace_id=trace_id,
            span_id=span.span_id,
            occurred_at=occurred,
            state="queued",
            available_at=now,
            deadline=now + timedelta(seconds=spec.wait_seconds),
            result={},
        )
        .on_conflict_do_nothing(constraint="uq_online_evaluation_target")
    )
    return True


def backfill_one(sessions, *, now=None, enabled=False):
    if not enabled:
        return {"status": "ONLINE_EXECUTION_DISABLED"}
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        candidate = session.execute(
            select(OnlineRule.id, OnlineRule.project_id)
            .where(OnlineRule.enabled.is_(True), OnlineRule.backfill_complete.is_(False))
            .order_by(OnlineRule.last_served_at, OnlineRule.id)
            .limit(1)
        ).first()
        if not candidate:
            return {"status": "IDLE"}
        from cutback.server.dataset_lineage import lock_lineage

        lock_lineage(session, candidate.project_id)
        rule = session.get(OnlineRule, candidate.id, with_for_update=True)
        if not rule or not rule.enabled or rule.backfill_complete:
            return {"status": "IDLE"}
        rule.last_served_at = now
        spec = OnlineRuleInput.model_validate(rule.specification)
        count = session.scalar(
            select(func.count())
            .select_from(OnlineEvaluation)
            .where(OnlineEvaluation.project_id == rule.project_id)
        )
        query = (
            select(ExecutionSpan, ExecutionTrace.trace_id)
            .join(ExecutionTrace)
            .where(
                ExecutionSpan.project_id == rule.project_id,
                ExecutionSpan.environment_id == rule.environment_id,
                ExecutionSpan.started_ns >= epoch_ns(spec.starts_at),
                ExecutionSpan.started_ns < epoch_ns(spec.expires_at),
            )
        )
        if rule.backfill_cursor:
            query = query.where(ExecutionSpan.id > rule.backfill_cursor)
        rows = session.execute(query.order_by(ExecutionSpan.id).limit(100)).all()
        for span, trace in rows:
            exists = session.scalar(
                select(OnlineEvaluation.id).where(
                    OnlineEvaluation.rule_id == rule.id, OnlineEvaluation.span_record_id == span.id
                )
            )
            if not exists and selected_time(rule, span) is not None:
                if count >= 50000:
                    return {"status": "PROJECT_ONLINE_QUEUE_FULL", "rule_id": str(rule.id)}
                enqueue(session, rule, span, trace, now)
                count += 1
            rule.backfill_cursor = span.id
        rule.backfill_complete = len(rows) < 100
        return {"status": "BACKFILLED", "examined": len(rows), "complete": rule.backfill_complete}


def queue_online_intake(sessions, *, enabled=False, now=None, only_project_id=None):
    if not enabled:
        return {"status": "ONLINE_EXECUTION_DISABLED"}
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        served = (
            select(func.max(OnlineRule.last_served_at))
            .where(OnlineRule.project_id == OnlineIntake.project_id)
            .correlate(OnlineIntake)
            .scalar_subquery()
        )
        query = (
            select(OnlineIntake.project_id)
            .group_by(OnlineIntake.project_id)
            .order_by(served, OnlineIntake.project_id)
        )
        if only_project_id:
            query = query.where(OnlineIntake.project_id == only_project_id)
        project = session.scalar(query.limit(1))
        if not project:
            return {"status": "IDLE"}
        from cutback.server.dataset_lineage import lock_lineage

        lock_lineage(session, project)
        rules = session.scalars(
            select(OnlineRule).where(OnlineRule.project_id == project).limit(1000)
        ).all()
        for rule in rules:
            rule.last_served_at = now
        count = session.scalar(
            select(func.count())
            .select_from(OnlineEvaluation)
            .where(OnlineEvaluation.project_id == project)
        )
        rows = session.execute(
            select(OnlineIntake, ExecutionSpan, ExecutionTrace.trace_id)
            .join(ExecutionSpan, OnlineIntake.span_record_id == ExecutionSpan.id)
            .join(ExecutionTrace, ExecutionSpan.trace_record_id == ExecutionTrace.id)
            .where(
                OnlineIntake.project_id == project,
                ExecutionSpan.project_id == project,
                ExecutionSpan.environment_id == OnlineIntake.environment_id,
                ExecutionTrace.project_id == project,
                ExecutionTrace.environment_id == OnlineIntake.environment_id,
            )
            .order_by(OnlineIntake.created_at, OnlineIntake.id)
            .with_for_update(of=OnlineIntake)
            .limit(20)
        ).all()
        for intake, span, trace in rows:
            existing = set(
                session.scalars(
                    select(OnlineEvaluation.rule_id).where(
                        OnlineEvaluation.span_record_id == span.id
                    )
                )
            )
            for rule in rules:
                if (
                    not rule.enabled
                    or rule.environment_id != intake.environment_id
                    or rule.id in existing
                    or selected_time(rule, span) is None
                ):
                    continue
                if count >= 50000:
                    return {"status": "PROJECT_ONLINE_QUEUE_FULL"}
                enqueue(session, rule, span, trace, now)
                count += 1
            from cutback.server.quality_service import select_quality_targets

            if not select_quality_targets(session, project, span, trace, now):
                return {"status": "PROJECT_QUALITY_QUEUE_FULL"}
            session.delete(intake)
        return {"status": "TARGETS_QUEUED", "spans_processed": len(rows)}


def claim_online(sessions, *, now=None, enabled=False):
    if not enabled:
        return None
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        # Least-recently-served rule, SKIP LOCKED: a busy tenant/rule cannot consume
        # the entire queue. Metadata-only crashed work is safe to recompute.
        eligible = (
            select(OnlineEvaluation.id)
            .where(
                OnlineEvaluation.rule_id == OnlineRule.id,
                or_(
                    (OnlineEvaluation.state == "queued") & (OnlineEvaluation.available_at <= now),
                    (OnlineEvaluation.state == "leased") & (OnlineEvaluation.lease_until <= now),
                ),
            )
            .exists()
        )
        candidate = session.execute(
            select(OnlineRule.project_id)
            .where(OnlineRule.enabled.is_(True), eligible)
            .group_by(OnlineRule.project_id)
            .order_by(func.max(OnlineRule.last_served_at), OnlineRule.project_id)
            .limit(1)
        ).first()
        if not candidate:
            return None
        from cutback.server.dataset_lineage import lock_lineage

        lock_lineage(session, candidate.project_id)
        rule = session.scalar(
            select(OnlineRule)
            .where(
                OnlineRule.enabled.is_(True),
                eligible,
                OnlineRule.project_id == candidate.project_id,
            )
            .order_by(OnlineRule.last_served_at, OnlineRule.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not rule:
            return None
        rule.last_served_at = now
        row = session.scalar(
            select(OnlineEvaluation)
            .where(
                OnlineEvaluation.rule_id == rule.id,
                or_(
                    (OnlineEvaluation.state == "queued") & (OnlineEvaluation.available_at <= now),
                    (OnlineEvaluation.state == "leased") & (OnlineEvaluation.lease_until <= now),
                ),
            )
            .order_by(OnlineEvaluation.available_at, OnlineEvaluation.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return None
        spec = OnlineRuleInput.model_validate(rule.specification)
        if not row.result.get("reserved"):
            if rule.evaluations_used >= spec.max_evaluations:
                row.state, row.result = "unknown", {"code": "EVALUATION_BUDGET_EXHAUSTED"}
                from cutback.server.alert_windows import touch_online_windows

                touch_online_windows(session, row)
                return None
            rule.evaluations_used += 1
        row.result = {"reserved": True}
        row.state, row.lease_token, row.lease_until = "leased", uuid4(), now + timedelta(seconds=30)
        return row.id, row.lease_token


def evaluate_online(session, row, rule, now):
    span = session.get(ExecutionSpan, row.span_record_id) if row.span_record_id else None
    if not span:
        return "unknown", "SOURCE_EXPIRED", None
    spec = OnlineRuleInput.model_validate(rule.specification)
    record = span.metadata_record
    if spec.evaluator.kind == "execution_ok":
        state = {
            "ok": "passed",
            "error": "failed",
            "cancelled": "unknown",
            "incomplete": "unknown",
            "unknown": "unknown",
        }[record["status"]]
        return state, "EXECUTION_STATUS_ONLY", None
    if spec.evaluator.kind == "duration_le":
        assert spec.evaluator.max_duration_ms is not None
        if record.get("ended_ns") is None:
            return "unknown", "DURATION_UNAVAILABLE", None
        duration = Decimal(record["ended_ns"] - record["started_ns"]) / Decimal(1000000)
        return (
            ("passed" if duration <= spec.evaluator.max_duration_ms else "failed"),
            "OBSERVED_SPAN_DURATION",
            str(duration),
        )
    query = select(ExternalEvaluation).where(
        ExternalEvaluation.project_id == rule.project_id,
        ExternalEvaluation.environment_id == rule.environment_id,
        ExternalEvaluation.source_origin == (spec.evaluator.source_origin or "external"),
        ExternalEvaluation.trace_id == row.trace_id,
        ExternalEvaluation.metadata_record["span_id"].as_string() == row.span_id,
        ExternalEvaluation.metadata_record["source"].as_string() == spec.evaluator.source,
        ExternalEvaluation.metadata_record["evaluator"].as_string() == spec.evaluator.evaluator,
        ExternalEvaluation.metadata_record["evaluator_version"].as_string()
        == spec.evaluator.evaluator_version,
    )
    results = session.scalars(query.limit(2)).all()
    if not results:
        return (
            ("waiting" if now < row.deadline else "missing"),
            "EXTERNAL_RESULT_NOT_OBSERVED",
            None,
        )
    if len(results) != 1:
        return "unknown", "AMBIGUOUS_EXTERNAL_RESULTS", None
    result = results[0].metadata_record
    if results[0].source_origin != "external" and result.get("source_status") == "missing":
        return "missing", "QUALITY_SOURCE_CONTENT_NOT_OBSERVED", str(results[0].result_id)
    if result["outcome"] in {"error", "unknown"}:
        return (
            result["outcome"],
            "EXTERNAL_EVALUATOR_" + result["outcome"].upper(),
            str(results[0].result_id),
        )
    if result["score_type"] != "boolean" or result["score"] != (result["outcome"] == "passed"):
        return "error", "EXTERNAL_BOOLEAN_CONTRACT_MISMATCH", str(results[0].result_id)
    return result["outcome"], "EXTERNALLY_ASSERTED_BOOLEAN", str(results[0].result_id)


def finish_online(sessions, claim, *, now=None):
    now = now or datetime.now(UTC)
    job_id, lease = claim
    with sessions.begin() as session:
        # Same lock order as claim: rule, then work receipt.
        from cutback.server.dataset_lineage import lock_lineage

        project = session.scalar(
            select(OnlineEvaluation.project_id).where(OnlineEvaluation.id == job_id)
        )
        if not project:
            return {"status": "STALE_LEASE"}
        lock_lineage(session, project)
        rule_id = session.scalar(
            select(OnlineEvaluation.rule_id).where(OnlineEvaluation.id == job_id)
        )
        rule = session.get(OnlineRule, rule_id, with_for_update=True) if rule_id else None
        row = session.get(OnlineEvaluation, job_id, with_for_update=True)
        if (
            not row
            or not rule
            or row.state != "leased"
            or row.lease_token != lease
            or row.lease_until <= now
        ):
            return {"status": "STALE_LEASE"}
        if not rule.enabled:
            row.state, row.result = "unknown", {"code": "RULE_DISABLED", "reserved": True}
        else:
            state, code, value = evaluate_online(session, row, rule, now)
            row.state = "queued" if state == "waiting" else state
            row.result = {
                "code": code,
                "value": value,
                "reserved": True,
                "cost_usd": "0",
                "evidence": "metadata_execution_or_external_assertion_not_task_success",
            }
            row.available_at = min(row.deadline, now + timedelta(seconds=15))
        row.lease_until, row.lease_token = None, None
        from cutback.server.alert_windows import touch_online_windows

        touch_online_windows(session, row)
        return {"status": row.state, "evaluation_id": str(row.id)}


def run_online_one(sessions, *, enabled=False, now=None):
    claim = claim_online(sessions, enabled=enabled, now=now)
    return (
        finish_online(sessions, claim, now=now)
        if claim
        else {"status": "IDLE" if enabled else "ONLINE_EXECUTION_DISABLED"}
    )
