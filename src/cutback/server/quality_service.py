"""Current content authority, protected-group exposure and provenance-separated scoring."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from cutback.core.traces import EvaluationResult
from cutback.evals.datasets import Case, digest
from cutback.server.content_policy import policy_content, require_payloads_enabled
from cutback.server.dataset_lineage import register_groups, require_unreserved_groups
from cutback.server.evaluation_api import active_policy
from cutback.server.lineage_db import DatasetExposure
from cutback.server.monitoring import epoch_ns, selected_time
from cutback.server.monitoring_db import OnlineRule
from cutback.server.principals import principal_role
from cutback.server.prompt_service import load_graph
from cutback.server.quality_contracts import JUDGE_SCHEMA, QualityRuleInput
from cutback.server.quality_db import QualityEvaluation, QualityRule
from cutback.server.trace_content import read_trace_content
from cutback.server.trace_db import ExecutionSpan, ExecutionTrace, ExternalEvaluation, TraceContent

MAX_QUALITY_RECEIPTS = 50000


def quality_authority(session, store, rule):
    require_payloads_enabled()
    spec = QualityRuleInput.model_validate(rule.specification)
    now = datetime.now(UTC)
    if not rule.enabled or now >= spec.expires_at:
        raise ValueError("QUALITY_RULE_DISABLED_OR_EXPIRED")
    parent = session.get(OnlineRule, rule.online_rule_id)
    if not parent or not parent.enabled:
        raise ValueError("QUALITY_SCORE_MONITOR_DISABLED")
    role = principal_role(session, rule.project_id, rule.creator)
    if role not in {"admin", "project_admin"}:
        raise ValueError("QUALITY_APPROVING_PRINCIPAL_NO_LONGER_AUTHORIZED")
    _, policy = active_policy(session, rule.project_id, spec.source_policy_id)
    if not policy.permits(spec.environment, spec.workflow):
        raise ValueError("QUALITY_SOURCE_POLICY_SCOPE_DENIED")
    if spec.judge and spec.judge.provider not in policy.allowed_providers:
        raise ValueError("QUALITY_JUDGE_VENDOR_NOT_APPROVED_FOR_SOURCE")
    graph = None
    if spec.prompt_version_id:
        graph = load_graph(
            session,
            store,
            rule.project_id,
            spec.prompt_version_id,
            {"actor": rule.creator, "role": role, "project_id": rule.project_id},
            env_name=spec.environment,
            workflow=spec.workflow,
            provider=spec.judge.provider if spec.judge else None,
            purpose="approved-online-quality",
        )
        if (
            rule.prompt_hash
            and graph.versions[spec.prompt_version_id].document_hash != rule.prompt_hash
        ):
            raise ValueError("QUALITY_PINNED_PROMPT_HASH_MISMATCH")
        document = graph.graph.documents[spec.prompt_version_id]
        if document.output_schema is None:
            raise ValueError("QUALITY_OUTPUT_SCHEMA_REQUIRED")
        if spec.evaluator == "model_judge":
            assert spec.judge is not None
            if document.output_schema != JUDGE_SCHEMA or graph.graph.inspect()["variables"] != {
                "input": "string",
                "output": "string",
            }:
                raise ValueError("QUALITY_JUDGE_SCHEMA_AND_INPUT_OUTPUT_VARIABLES_REQUIRED")
            graph.graph.native_contract(
                {"output": "synthetic-bound-validation"}, surface=spec.judge.surface
            )
    return spec, policy, graph


def quality_case(session, store, rule, row):
    spec, policy, graph = quality_authority(session, store, rule)
    span = session.get(ExecutionSpan, row.span_record_id) if row.span_record_id else None
    if not span or span.project_id != rule.project_id or span.environment_id != rule.environment_id:
        raise ValueError("QUALITY_SOURCE_SPAN_EXPIRED")
    link = session.scalar(
        select(TraceContent).where(
            TraceContent.project_id == rule.project_id,
            TraceContent.environment_id == rule.environment_id,
            TraceContent.trace_record_id == span.trace_record_id,
            TraceContent.span_id == row.span_id,
        )
    )
    if not link:
        return None
    from cutback.server.db import PayloadSample

    payload = session.get(PayloadSample, link.payload_id)
    if not payload or payload.policy_id != spec.source_policy_id:
        raise ValueError("QUALITY_SOURCE_POLICY_IDENTITY_MISMATCH")
    source = read_trace_content(
        SimpleNamespace(state=SimpleNamespace(artifact_store=store)),
        session,
        rule.project_id,
        link,
        rule.creator,
        "approved-online-quality",
    )["content"]
    # Online feedback must not recycle protected confirmation examples. The
    # normalized captured input joins the same persistent project family registry.
    if not isinstance(source.get("input"), str) or not source["input"]:
        raise ValueError("QUALITY_INPUT_CONTEXT_OR_LINEAGE_UNAVAILABLE")
    if not isinstance(source.get("output"), str):
        raise ValueError("QUALITY_OUTPUT_NOT_CAPTURED")
    families = register_groups(
        session, rule.project_id, [Case(id=str(row.id), input=source["input"])], actor=rule.creator
    )
    require_unreserved_groups(session, rule.project_id, set(families.values()))
    session.execute(
        insert(DatasetExposure)
        .values(
            id=uuid4(),
            project_id=rule.project_id,
            family_id=families[str(row.id)],
            dataset_id=None,
            run_id=None,
            source_ref="online-quality:" + str(row.id),
            mode="validation",
            protected=False,
        )
        .on_conflict_do_nothing(constraint="uq_dataset_exposure")
    )
    if graph:
        for component_policy in graph.policies.values():
            policy_content(
                {"variables": {"input": source["input"], "output": source["output"]}},
                component_policy,
                preserve_semantics=True,
            )
    return spec, policy, graph, source, payload


def enqueue_quality_span(session, rule, span, trace, now):
    # Matching/sampling uses the exact same rule UUID as its online score monitor.
    parent = session.get(OnlineRule, rule.online_rule_id)
    occurred = selected_time(parent, span) if parent and parent.enabled else None
    if occurred is None or not rule.enabled:
        return False
    existing = session.scalar(
        select(QualityEvaluation.id).where(
            QualityEvaluation.rule_id == rule.id, QualityEvaluation.span_record_id == span.id
        )
    )
    if existing:
        return False
    # Admission records the frozen scope even after a price catalog change.
    # Claim/reservation validate current authority and seal an explicit unknown receipt.
    specification = rule.specification
    session.add(
        QualityEvaluation(
            project_id=rule.project_id,
            rule_id=rule.id,
            span_record_id=span.id,
            trace_id=trace,
            span_id=span.span_id,
            occurred_at=occurred,
            available_at=now,
            deadline=min(
                datetime.fromisoformat(specification["expires_at"]),
                now + timedelta(seconds=specification["wait_seconds"]),
            ),
            source_metadata={
                key: span.metadata_record.get(key)
                for key in ("workflow", "service", "release", "kind")
            },
            result={},
        )
    )
    return True


def select_quality_targets(session, project, span, trace, now):
    rules = session.scalars(
        select(QualityRule)
        .where(
            QualityRule.project_id == project,
            QualityRule.environment_id == span.environment_id,
            QualityRule.enabled.is_(True),
        )
        .limit(50)
    ).all()
    count = session.scalar(
        select(func.count())
        .select_from(QualityEvaluation)
        .where(QualityEvaluation.project_id == project)
    )
    for rule in rules:
        exists = session.scalar(
            select(QualityEvaluation.id).where(
                QualityEvaluation.rule_id == rule.id, QualityEvaluation.span_record_id == span.id
            )
        )
        if exists:
            continue
        parent = session.get(OnlineRule, rule.online_rule_id)
        if not parent or not parent.enabled or selected_time(parent, span) is None:
            continue
        if count >= MAX_QUALITY_RECEIPTS:
            return False
        count += int(enqueue_quality_span(session, rule, span, trace, now))
    return True


def publish_quality_score(session, rule, row, state, code, *, now=None):
    now = now or datetime.now(UTC)
    if not row.charge_started and row.spent_usd is None:
        from decimal import Decimal

        row.spent_usd = Decimal(0)
    origin = (
        "cutback_model_judge"
        if rule.specification.get("evaluator") == "model_judge"
        else "cutback_technical_contract"
    )
    result = EvaluationResult(
        result_id=row.id,
        source="cutback-online-quality",
        evaluator=rule.key,
        evaluator_version=str(rule.id),
        trace_id=row.trace_id,
        span_id=row.span_id,
        score_type="boolean" if state in {"passed", "failed"} else "none",
        score=(state == "passed") if state in {"passed", "failed"} else None,
        outcome="unknown" if state == "missing" else state,
        error_code=code if state == "error" else None,
        observed_at=now,
    )
    record = result.model_dump(mode="json")
    if state == "missing":
        record["source_status"] = "missing"
    session.execute(
        insert(ExternalEvaluation)
        .values(
            id=uuid4(),
            project_id=rule.project_id,
            environment_id=rule.environment_id,
            result_id=row.id,
            source_origin=origin,
            trace_id=row.trace_id,
            session_id=None,
            fingerprint=digest(record),
            metadata_record=record,
        )
        .on_conflict_do_nothing(constraint="uq_external_evaluation")
    )
    # Wake a waiting exact-source monitor without resetting an already terminal
    # receipt or manufacturing a new denominator.
    from sqlalchemy import update

    from cutback.server.monitoring_db import OnlineEvaluation

    session.execute(
        update(OnlineEvaluation)
        .where(
            OnlineEvaluation.rule_id == rule.online_rule_id,
            OnlineEvaluation.span_record_id == row.span_record_id,
            OnlineEvaluation.state == "queued",
        )
        .values(available_at=now)
    )
    row.state, row.completed_at = state, now
    row.result = {"code": code, "evidence": origin, "independent_task_success": "not_verified"}
    row.lease_token, row.lease_until = None, None


def backfill_quality_one(sessions, *, enabled=False, now=None):
    if not enabled:
        return {"status": "QUALITY_EXECUTION_DISABLED"}
    now = now or datetime.now(UTC)
    from cutback.server.dataset_lineage import lock_lineage

    with sessions.begin() as session:
        candidate = session.execute(
            select(QualityRule.id, QualityRule.project_id)
            .where(QualityRule.enabled.is_(True), QualityRule.backfill_complete.is_(False))
            .order_by(QualityRule.last_served_at, QualityRule.id)
            .limit(1)
        ).first()
        if not candidate:
            return {"status": "IDLE"}
        lock_lineage(session, candidate.project_id)
        rule = session.get(QualityRule, candidate.id, with_for_update=True)
        if not rule or not rule.enabled or rule.backfill_complete:
            return {"status": "IDLE"}
        rule.last_served_at = now
        try:
            spec = QualityRuleInput.model_validate(rule.specification)
        except ValueError:
            rule.enabled = False
            rule.backfill_complete = True
            parent = session.get(OnlineRule, rule.online_rule_id)
            if parent:
                parent.enabled = False
            from cutback.server.db import AuditEvent

            session.add(
                AuditEvent(
                    project_id=rule.project_id,
                    actor="quality_worker",
                    action="quality_reapproval_required",
                    details={"rule_id": str(rule.id)},
                )
            )
            return {"status": "QUALITY_CONTRACT_REAPPROVAL_REQUIRED"}
        if now >= spec.expires_at:
            rule.backfill_complete = True
            return {"status": "QUALITY_RULE_EXPIRED"}
        count = session.scalar(
            select(func.count())
            .select_from(QualityEvaluation)
            .where(QualityEvaluation.project_id == rule.project_id)
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
            existing = session.scalar(
                select(QualityEvaluation.id).where(
                    QualityEvaluation.rule_id == rule.id,
                    QualityEvaluation.span_record_id == span.id,
                )
            )
            parent = session.get(OnlineRule, rule.online_rule_id)
            selected = parent and parent.enabled and selected_time(parent, span) is not None
            if count >= MAX_QUALITY_RECEIPTS and not existing and selected:
                return {"status": "PROJECT_QUALITY_QUEUE_FULL"}
            count += int(enqueue_quality_span(session, rule, span, trace, now))
            rule.backfill_cursor = span.id
        rule.backfill_complete = len(rows) < 100
        return {"status": "BACKFILLED", "examined": len(rows), "complete": rule.backfill_complete}
