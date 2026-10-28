"""One-shot online judging through the canonical ledger. No charged lease is retried."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select

from cutback.evals.datasets import digest
from cutback.evals.experiments import Budget, BudgetLedger
from cutback.server.content_policy import policy_content
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, PayloadSample
from cutback.server.evaluation_api import policy_object_count
from cutback.server.quality_contracts import QualityRuleInput
from cutback.server.quality_db import QualityEvaluation, QualityRule
from cutback.server.quality_metrics import input_bucket, judge_verdict, output_contract
from cutback.server.quality_service import publish_quality_score, quality_case


def current_job(session, identifier, token, now):
    project = session.scalar(
        select(QualityEvaluation.project_id).where(QualityEvaluation.id == identifier)
    )
    if not project:
        raise ValueError("QUALITY_LEASE_UNAVAILABLE")
    lock_lineage(session, project)
    row = session.get(QualityEvaluation, identifier, with_for_update=True)
    rule = session.get(QualityRule, row.rule_id, with_for_update=True) if row else None
    if (
        not row
        or not rule
        or row.state != "leased"
        or row.lease_token != token
        or not row.lease_until
        or row.lease_until <= now
    ):
        raise ValueError("QUALITY_LEASE_UNAVAILABLE")
    return rule, row


def claim_quality(sessions, *, enabled=False, now=None):
    if not enabled:
        return None
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        eligible = (
            select(QualityEvaluation.id)
            .where(
                QualityEvaluation.rule_id == QualityRule.id,
                or_(
                    (QualityEvaluation.state == "queued") & (QualityEvaluation.available_at <= now),
                    (QualityEvaluation.state == "leased") & (QualityEvaluation.lease_until <= now),
                ),
            )
            .exists()
        )
        candidate = session.execute(
            select(QualityRule.id, QualityRule.project_id)
            .where(eligible)
            .order_by(QualityRule.last_served_at, QualityRule.id)
            .limit(1)
        ).first()
        if not candidate:
            return None
        lock_lineage(session, candidate.project_id)
        rule = session.get(QualityRule, candidate.id, with_for_update=True)
        if not rule:
            return None
        row = session.scalar(
            select(QualityEvaluation)
            .where(
                QualityEvaluation.rule_id == rule.id,
                or_(
                    (QualityEvaluation.state == "queued") & (QualityEvaluation.available_at <= now),
                    (QualityEvaluation.state == "leased") & (QualityEvaluation.lease_until <= now),
                ),
            )
            .order_by(QualityEvaluation.available_at, QualityEvaluation.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return None
        rule.last_served_at = now
        if row.charge_started:
            row.output_reserved = False
            publish_quality_score(
                session, rule, row, "unknown", "QUALITY_LEASE_EXPIRED_BILLING_UNCERTAIN", now=now
            )
            return {"terminal": True, "status": "unknown", "evaluation_id": str(row.id)}
        try:
            spec = QualityRuleInput.model_validate(rule.specification)
        except ValueError:
            publish_quality_score(
                session, rule, row, "unknown", "QUALITY_CONTRACT_REAPPROVAL_REQUIRED", now=now
            )
            return {"terminal": True, "status": "unknown", "evaluation_id": str(row.id)}
        if not rule.enabled or now >= spec.expires_at:
            publish_quality_score(
                session, rule, row, "unknown", "QUALITY_RULE_DISABLED_OR_EXPIRED", now=now
            )
            return {"terminal": True, "status": "unknown", "evaluation_id": str(row.id)}
        if now >= row.deadline:
            configuration_missing = (
                row.result.get("code") == "QUALITY_JUDGE_CONFIGURATION_UNAVAILABLE"
            )
            publish_quality_score(
                session,
                rule,
                row,
                "unknown" if configuration_missing else "missing",
                "QUALITY_JUDGE_CONFIGURATION_DEADLINE"
                if configuration_missing
                else "QUALITY_ADMISSION_DEADLINE_EXPIRED",
                now=now,
            )
            return {"terminal": True, "status": row.state, "evaluation_id": str(row.id)}
        if not row.evaluation_reserved:
            if rule.evaluations_used >= spec.max_evaluations:
                publish_quality_score(
                    session, rule, row, "unknown", "QUALITY_EVALUATION_COUNT_EXHAUSTED", now=now
                )
                return {"terminal": True, "status": "unknown", "evaluation_id": str(row.id)}
            rule.evaluations_used += 1
            row.evaluation_reserved = True
        row.state, row.lease_token, row.lease_until = (
            "leased",
            uuid4(),
            now + timedelta(seconds=120),
        )
        return {"id": row.id, "token": row.lease_token}


def reserve_quality(sessions, store, claim, snapshot, amount):
    now = datetime.now(UTC)
    with sessions.begin() as session:
        rule, row = current_job(session, claim["id"], claim["token"], now)
        if now >= row.deadline:
            raise ValueError("QUALITY_ADMISSION_DEADLINE_EXPIRED")
        context = quality_case(session, store, rule, row)
        if context is None or context[3] != snapshot:
            raise ValueError("QUALITY_SOURCE_OR_POLICY_CHANGED_BEFORE_CALL")
        spec, policy, _, _, _ = context
        if row.charge_started or spec.evaluator != "model_judge":
            raise ValueError("QUALITY_REQUEST_ALREADY_RESERVED_OR_NOT_APPROVED")
        if (
            rule.requests_used >= spec.max_requests
            or rule.reserved_usd + amount > spec.max_total_usd
        ):
            raise ValueError("QUALITY_TOTAL_REQUEST_OR_DOLLAR_BUDGET_EXHAUSTED")
        if spec.retain_judge_output:
            if policy_object_count(session, spec.source_policy_id) >= policy.max_samples:
                raise ValueError("QUALITY_OUTPUT_CAPTURE_QUOTA_EXHAUSTED")
            row.output_reserved = True
        row.charge_started, row.reserved_usd = True, amount
        rule.requests_used += 1
        rule.reserved_usd += amount
        session.add(
            AuditEvent(
                project_id=rule.project_id,
                actor="quality_worker",
                action="quality_request_reserved",
                details={
                    "rule_id": str(rule.id),
                    "evaluation_id": str(row.id),
                    "upper_usd": str(amount),
                    "price_sha256": spec.price_sha256,
                },
            )
        )


def finish_quality(
    sessions, store, claim, state, code, *, snapshot=None, spent=None, raw=None, now=None
):
    now = now or datetime.now(UTC)
    created = None
    created_project = None
    try:
        with sessions.begin() as session:
            rule, row = current_job(session, claim["id"], claim["token"], now)
            context = None
            if snapshot is not None:
                try:
                    context = quality_case(session, store, rule, row)
                    if context is None or context[3] != snapshot:
                        raise ValueError("QUALITY_SOURCE_POLICY_CHANGED")
                except (ValueError, HTTPException, OSError):
                    state, code = "unknown", "QUALITY_CURRENT_CONTENT_AUTHORITY_UNAVAILABLE"
            if not row.charge_started:
                spent = Decimal(0)
            row.spent_usd = spent
            if spent is not None:
                rule.spent_usd += spent
                if row.charge_started:
                    rule.known_cost_requests += 1
            if context:
                spec, policy, _, source, _ = context
                row.source_metadata = {
                    **row.source_metadata,
                    "source_evidence": "synthetic_capture"
                    if policy.rights_basis == "synthetic"
                    else "owner_authorized_capture_not_independent_gold",
                }
                if spec.record_input_features:
                    row.source_metadata = {
                        **row.source_metadata,
                        "input_size_bucket": input_bucket(source["input"]),
                    }
                if raw is not None and spec.retain_judge_output and row.output_reserved:
                    value = {"output": raw, "content_nonce": str(uuid4())}
                    try:
                        clean, blob = policy_content(value, policy)
                    except ValueError:
                        state, code = "error", "QUALITY_RESPONSE_RETENTION_POLICY_REJECTED"
                    else:
                        created = store.put(rule.project_id, blob, policy.retention_days)
                        created_project = rule.project_id
                        payload = PayloadSample(
                            project_id=rule.project_id,
                            policy_id=spec.source_policy_id,
                            object_ref=created,
                            content_hash=digest(clean),
                            expires_at=min(
                                policy.expires_at, now + timedelta(days=policy.retention_days)
                            ),
                            metadata_safe={
                                "kind": "online_quality_response",
                                "environment": spec.environment,
                                "workflow": spec.workflow,
                                "quality_evaluation_id": str(row.id),
                            },
                        )
                        session.add(payload)
                        session.flush()
                        row.output_payload_id = payload.id
            row.output_reserved = False
            publish_quality_score(session, rule, row, state, code, now=now)
            session.add(
                AuditEvent(
                    project_id=rule.project_id,
                    actor="quality_worker",
                    action="quality_evaluation_finished",
                    details={
                        "rule_id": str(rule.id),
                        "evaluation_id": str(row.id),
                        "outcome": state,
                        "code": code,
                    },
                )
            )
            return {"status": state, "evaluation_id": str(row.id), "code": code}
    except BaseException:
        if created:
            # A lost COMMIT acknowledgement is not proof of rollback. Preserve
            # a possibly referenced encrypted object when DB state is unavailable.
            try:
                with sessions() as session:
                    referenced = session.scalar(
                        select(PayloadSample.id).where(
                            PayloadSample.project_id == created_project,
                            PayloadSample.object_ref == created,
                        )
                    )
                if not referenced:
                    store.delete(created_project, created)
            except Exception:
                pass
        raise


def run_quality_one(sessions, store, *, execution_enabled=False, executor=None, now=None):
    frozen_now = now
    known_spent = None
    if not execution_enabled:
        return {"status": "QUALITY_EXECUTION_DISABLED", "provider_calls": 0}
    claim = claim_quality(sessions, enabled=True, now=now)
    if not claim:
        return {"status": "IDLE"}
    if claim.get("terminal"):
        return claim
    now = now or datetime.now(UTC)
    try:
        with sessions.begin() as session:
            rule, row = current_job(session, claim["id"], claim["token"], now)
            context = quality_case(session, store, rule, row)
            if context is None:
                if now < row.deadline:
                    row.state, row.available_at = (
                        "queued",
                        min(row.deadline, now + timedelta(seconds=15)),
                    )
                    row.lease_token, row.lease_until = None, None
                    return {"status": "waiting_for_capture", "evaluation_id": str(row.id)}
                row.spent_usd = Decimal(0)
                publish_quality_score(
                    session, rule, row, "missing", "QUALITY_SOURCE_CONTENT_NOT_OBSERVED", now=now
                )
                return {"status": "missing", "evaluation_id": str(row.id)}
            spec, policy, graph, source, payload = context
        if spec.evaluator != "model_judge":
            schema = graph.graph.documents[spec.prompt_version_id].output_schema if graph else None
            state, code = output_contract(source["output"], schema)
            return finish_quality(
                sessions,
                store,
                claim,
                state,
                code,
                snapshot=source,
                spent=Decimal(0),
                now=frozen_now,
            )
        assert spec.judge is not None and graph is not None
        model = spec.judge.model_spec().model_copy(
            update={
                "request_contract": graph.graph.native_contract(
                    {"output": source["output"]}, surface=spec.judge.surface
                )
            }
        )
        owned = executor is None
        if owned:
            from cutback.evals.executors import ConfiguredExecutor

            try:
                executor = ConfiguredExecutor()
                executor.prepare(model.provider)
            except (ValueError, ImportError, KeyError):
                with sessions.begin() as session:
                    _, row = current_job(session, claim["id"], claim["token"], now)
                    if now >= row.deadline:
                        expired_configuration = True
                    else:
                        expired_configuration = False
                    if not expired_configuration:
                        row.result = {"code": "QUALITY_JUDGE_CONFIGURATION_UNAVAILABLE"}
                        row.state, row.available_at = (
                            "queued",
                            min(row.deadline, now + timedelta(seconds=60)),
                        )
                        row.lease_token, row.lease_until = None, None
                if expired_configuration:
                    return finish_quality(
                        sessions,
                        store,
                        claim,
                        "unknown",
                        "QUALITY_JUDGE_CONFIGURATION_DEADLINE",
                        snapshot=source,
                        now=frozen_now,
                    )
                return {"status": "blocked_missing_judge_configuration"}
        spent, raw = None, None
        try:
            ledger = BudgetLedger(
                Budget(
                    max_usd=spec.max_total_usd, max_requests=spec.max_requests, max_wall_seconds=60
                ),
                reserve=lambda amount: reserve_quality(sessions, store, claim, source, amount),
                price_sha256=spec.price_sha256,
            )
            result = ledger.call(executor, model, source["input"], traffic_kind="judge")
            spent, raw = result.cost_usd, result.output
            known_spent = spent
            state, code = (
                judge_verdict(raw, source["input"], source["output"])
                if result.complete
                else ("error", "QUALITY_JUDGE_NATIVE_RESPONSE_INCOMPLETE")
            )
        except Exception:
            with sessions() as session:
                record = session.get(QualityEvaluation, claim["id"])
                started = bool(record and record.charge_started)
            state, code = (
                ("error", "QUALITY_JUDGE_EXECUTION_OR_BILLING_UNCERTAIN")
                if started
                else ("unknown", "QUALITY_REQUEST_NOT_STARTED_BUDGET_OR_AUTHORITY")
            )
        finally:
            if owned:
                executor.close()
        return finish_quality(
            sessions,
            store,
            claim,
            state,
            code,
            snapshot=source,
            spent=spent,
            raw=raw,
            now=frozen_now,
        )
    except (ValueError, HTTPException, OSError):
        try:
            return finish_quality(
                sessions,
                store,
                claim,
                "unknown",
                "QUALITY_SOURCE_OR_AUTHORITY_UNAVAILABLE",
                spent=known_spent,
                now=frozen_now,
            )
        except (ValueError, HTTPException, OSError):
            return {"status": "stale_or_unavailable"}
