"""Explicitly approved, project-scoped online content evaluation authority."""

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cryptography.exceptions import InvalidTag
from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select, update

from cutback.evals.datasets import digest
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, PayloadSample
from cutback.server.evaluation_api import ContentPurpose, active_policy, require_store
from cutback.server.monitoring_api import scoped
from cutback.server.monitoring_contracts import (
    OnlineEvaluator,
    OnlineRuleInput,
    RuleSwitch,
    SpanFilter,
)
from cutback.server.monitoring_db import OnlineEvaluation, OnlineRule
from cutback.server.prompt_service import environment
from cutback.server.quality_contracts import QualityRuleInput
from cutback.server.quality_db import QualityEvaluation, QualityPromptUse, QualityRule
from cutback.server.quality_reports import QualityComparison, compare_quality
from cutback.server.quality_service import quality_authority
from cutback.server.trace_db import ExecutionSpan


def rule_metadata(row):
    return {
        "id": str(row.id),
        "key": row.key,
        "revision": row.revision,
        "environment_id": str(row.environment_id),
        "online_rule_id": str(row.online_rule_id),
        "enabled": row.enabled,
        "specification": row.specification,
        "prompt_hash": row.prompt_hash,
        "approval_hash": row.approval_hash,
        "evaluations_used": row.evaluations_used,
        "requests_used": row.requests_used,
        "known_cost_requests": row.known_cost_requests,
        "unknown_or_pending_cost_requests": row.requests_used - row.known_cost_requests,
        "reserved_upper_usd": str(row.reserved_usd),
        "known_spent_usd": str(row.spent_usd),
        "cost_coverage": "completed_known_calls_only",
        "backfill_complete": row.backfill_complete,
        "created_at": row.created_at,
    }


def evaluation_metadata(session, row):
    trace = (
        session.scalar(
            select(ExecutionSpan.trace_record_id).where(
                ExecutionSpan.id == row.span_record_id, ExecutionSpan.project_id == row.project_id
            )
        )
        if row.span_record_id
        else None
    )
    return {
        "id": str(row.id),
        "rule_id": str(row.rule_id),
        "trace_id": row.trace_id,
        "span_id": row.span_id,
        "trace_record_id": str(trace) if trace else None,
        "state": row.state,
        "source_metadata": row.source_metadata,
        "occurred_at": row.occurred_at,
        "result": row.result,
        "request_reserved": row.charge_started,
        "reserved_upper_usd": str(row.reserved_usd),
        "known_spent_usd": str(row.spent_usd) if row.spent_usd is not None else None,
        "output_retained": row.output_payload_id is not None,
        "completed_at": row.completed_at,
    }


def install_quality(app, sessions, authorize, scope):
    @app.post("/v1/projects/{project_id}/quality-rules/{rule_id}/compare")
    def compare(project_id: UUID, rule_id: UUID, body: QualityComparison, auth=Depends(authorize)):
        scope(auth, project_id)
        with sessions() as session:
            row = scoped(session, QualityRule, project_id, rule_id)
            try:
                return compare_quality(session, row, body)
            except ValueError:
                raise HTTPException(422, "QUALITY_COHORT_EXCEEDS_RECEIPT_BOUND") from None

    @app.post("/v1/projects/{project_id}/quality-rules", status_code=201)
    def create_rule(project_id: UUID, body: QualityRuleInput, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        now = datetime.now(UTC)
        if (
            body.starts_at < now - timedelta(days=30)
            or body.starts_at > now + timedelta(days=1)
            or body.expires_at <= now
        ):
            raise HTTPException(422, "QUALITY_RULE_WINDOW_OUTSIDE_ALLOWED_RANGE")
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            existing = session.get(QualityRule, body.version_id)
            if existing:
                if existing.project_id == project_id and existing.specification == body.model_dump(
                    mode="json"
                ):
                    return rule_metadata(existing)
                raise HTTPException(409, "QUALITY_VERSION_IDENTITY_CONFLICT")
            if session.get(OnlineRule, body.version_id):
                raise HTTPException(409, "QUALITY_VERSION_IDENTITY_CONFLICT")
            previous = session.scalar(
                select(QualityRule)
                .where(QualityRule.project_id == project_id, QualityRule.key == body.key)
                .order_by(QualityRule.revision.desc())
                .limit(1)
            )
            if (previous.revision if previous else 0) != body.expected_revision:
                raise HTTPException(409, "QUALITY_REVISION_CONFLICT")
            if previous:
                previous.enabled = False
                parent = session.get(OnlineRule, previous.online_rule_id)
                parent.enabled = False
                session.execute(
                    update(OnlineEvaluation)
                    .where(
                        OnlineEvaluation.rule_id == parent.id, OnlineEvaluation.state == "queued"
                    )
                    .values(state="unknown", result={"code": "QUALITY_RULE_SUPERSEDED"})
                )
            env = environment(session, project_id, body.environment)
            active = session.scalar(
                select(func.count())
                .select_from(OnlineRule)
                .where(
                    OnlineRule.project_id == project_id,
                    OnlineRule.environment_id == env.id,
                    OnlineRule.enabled.is_(True),
                )
            )
            total = session.scalar(
                select(func.count())
                .select_from(OnlineRule)
                .where(OnlineRule.project_id == project_id)
            )
            if active >= 50 or total >= 1000:
                raise HTTPException(429, "PROJECT_ONLINE_RULE_QUOTA_EXCEEDED")
            origin = (
                "cutback_model_judge"
                if body.evaluator == "model_judge"
                else "cutback_technical_contract"
            )
            parent_spec = OnlineRuleInput(
                version_id=body.version_id,
                key="quality." + body.key[:100] + "." + str(body.version_id),
                expected_revision=0,
                environment=body.environment,
                filter=SpanFilter.model_validate(
                    {**body.filter.model_dump(), "workflow": body.workflow}
                ),
                evaluator=OnlineEvaluator.model_validate(
                    {
                        "kind": "external_boolean",
                        "source": "cutback-online-quality",
                        "evaluator": body.key,
                        "evaluator_version": str(body.version_id),
                        "source_origin": origin,
                    }
                ),
                starts_at=body.starts_at,
                expires_at=body.expires_at,
                sample_basis_points=body.sample_basis_points,
                max_evaluations=body.max_evaluations,
                max_total_usd=Decimal(0),
                wait_seconds=86400,
                approval_ref=body.approval_ref,
            )
            parent = OnlineRule(
                id=body.version_id,
                project_id=project_id,
                environment_id=env.id,
                key=parent_spec.key,
                revision=1,
                specification=parent_spec.model_dump(mode="json"),
                creator=auth["actor"],
            )
            session.add(parent)
            session.flush()
            row = QualityRule(
                id=body.version_id,
                project_id=project_id,
                environment_id=env.id,
                online_rule_id=parent.id,
                key=body.key,
                revision=body.expected_revision + 1,
                specification=body.model_dump(mode="json"),
                creator=auth["actor"],
                approval_hash="pending",
                enabled=True,
            )
            session.add(row)
            session.flush()
            try:
                _, policy, graph = quality_authority(session, store, row)
            except ValueError:
                raise HTTPException(
                    422, "QUALITY_CONTENT_MODEL_OR_PROMPT_CONTRACT_INVALID"
                ) from None
            if body.expires_at > policy.expires_at or graph and body.expires_at > graph.expires_at:
                raise HTTPException(422, "QUALITY_RULE_EXCEEDS_CURRENT_CONTENT_AUTHORITY_EXPIRY")
            if graph:
                row.prompt_hash = graph.versions[body.prompt_version_id].document_hash
                session.add_all(
                    [
                        QualityPromptUse(project_id=project_id, rule_id=row.id, version_id=v)
                        for v in graph.versions
                    ]
                )
            row.approval_hash = digest(
                {"specification": row.specification, "prompt_hash": row.prompt_hash}
            )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="quality_rule_approved",
                    details={
                        "rule_id": str(row.id),
                        "approval_hash": row.approval_hash,
                        "evaluator": body.evaluator,
                    },
                )
            )
            return rule_metadata(row)

    @app.get("/v1/projects/{project_id}/quality-rules")
    def rules(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            query = select(QualityRule).where(QualityRule.project_id == project_id)
            if after:
                query = query.where(QualityRule.id > after)
            rows = session.scalars(query.order_by(QualityRule.id).limit(limit + 1)).all()
            return {
                "items": [rule_metadata(r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "collector_quality_execution_setting": os.getenv(
                    "CUTBACK_QUALITY_EXECUTION", "false"
                ).lower()
                == "true",
                "worker_execution_state": "not_observed_by_collector",
            }

    @app.post("/v1/projects/{project_id}/quality-rules/{rule_id}/switch")
    def switch(project_id: UUID, rule_id: UUID, body: RuleSwitch, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            lock_lineage(session, project_id)
            row = scoped(session, QualityRule, project_id, rule_id, lock=True)
            latest = session.scalar(
                select(func.max(QualityRule.revision)).where(
                    QualityRule.project_id == project_id, QualityRule.key == row.key
                )
            )
            if (
                body.expected_revision != row.revision
                or body.expected_enabled != row.enabled
                or latest != row.revision
            ):
                raise HTTPException(409, "QUALITY_SWITCH_REVISION_CONFLICT")
            parent = scoped(session, OnlineRule, project_id, row.online_rule_id, lock=True)
            if body.enabled:
                active = session.scalar(
                    select(func.count())
                    .select_from(OnlineRule)
                    .where(
                        OnlineRule.project_id == project_id,
                        OnlineRule.environment_id == row.environment_id,
                        OnlineRule.enabled.is_(True),
                        OnlineRule.id != parent.id,
                    )
                )
                if active >= 50:
                    raise HTTPException(429, "ENVIRONMENT_ONLINE_RULE_QUOTA_EXCEEDED")
                row.enabled = True
                try:
                    quality_authority(session, require_store(app), row)
                except ValueError:
                    raise HTTPException(409, "QUALITY_AUTHORITY_REAPPROVAL_REQUIRED") from None
            row.enabled, parent.enabled = body.enabled, body.enabled
            if not body.enabled:
                session.execute(
                    update(OnlineEvaluation)
                    .where(
                        OnlineEvaluation.rule_id == parent.id, OnlineEvaluation.state == "queued"
                    )
                    .values(state="unknown", result={"code": "QUALITY_RULE_DISABLED"})
                )
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="quality_rule_switched",
                    details={"rule_id": str(row.id), "enabled": row.enabled},
                )
            )
            return rule_metadata(row)

    @app.get("/v1/projects/{project_id}/quality-rules/{rule_id}/evaluations")
    def evaluations(
        project_id: UUID,
        rule_id: UUID,
        after: UUID | None = None,
        limit: int = Query(50, ge=1, le=100),
        auth=Depends(authorize),
    ):
        scope(auth, project_id)
        with sessions() as session:
            scoped(session, QualityRule, project_id, rule_id)
            query = select(QualityEvaluation).where(QualityEvaluation.rule_id == rule_id)
            if after:
                query = query.where(QualityEvaluation.id > after)
            rows = session.scalars(query.order_by(QualityEvaluation.id).limit(limit + 1)).all()
            return {
                "items": [evaluation_metadata(session, r) for r in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                "coverage": "retained_selected_sources_only",
                "independent_task_quality": "not_verified",
            }

    @app.post("/v1/projects/{project_id}/quality-evaluations/{evaluation_id}/output")
    def read_output(
        project_id: UUID, evaluation_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ):
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            row = scoped(session, QualityEvaluation, project_id, evaluation_id)
            payload = (
                session.get(PayloadSample, row.output_payload_id) if row.output_payload_id else None
            )
            if (
                not payload
                or payload.project_id != project_id
                or payload.expires_at <= datetime.now(UTC)
                or payload.metadata_safe.get("deleted")
            ):
                raise HTTPException(410, "QUALITY_OUTPUT_NOT_RETAINED_OR_EXPIRED")
            rule = scoped(session, QualityRule, project_id, row.rule_id)
            # Reading historical content rechecks current rights, not current model prices.
            spec = rule.specification
            _, policy = active_policy(session, project_id, payload.policy_id)
            if (
                str(payload.policy_id) != spec["source_policy_id"]
                or not policy.permits(spec["environment"], spec["workflow"])
                or spec.get("judge")
                and spec["judge"]["provider"] not in policy.allowed_providers
            ):
                raise HTTPException(403, "QUALITY_OUTPUT_CURRENT_POLICY_SCOPE_DENIED")
            try:
                value = json.loads(store.get(project_id, payload.object_ref))
                if digest(value) != payload.content_hash:
                    raise ValueError
                clean, _ = policy_content(value, policy)
            except (ValueError, OSError, InvalidTag):
                raise HTTPException(410, "QUALITY_OUTPUT_UNAVAILABLE") from None
            session.add(
                AuditEvent(
                    project_id=project_id,
                    actor=auth["actor"],
                    action="quality_output_read",
                    details={"evaluation_id": str(row.id), "purpose": body.purpose},
                )
            )
            return {
                "output": clean.get("output"),
                "evidence": "model_judge_not_independent_gold",
                "expires_at": payload.expires_at,
            }
