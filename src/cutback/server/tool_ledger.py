"""Commit isolated-session/operation intent before crossing the tool boundary."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from cutback.core.isolated_episodes import GuardedRecordExecution
from cutback.evals.datasets import digest
from cutback.server.db import Environment
from cutback.server.execution_db import ExecutionEpisode
from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord


class ToolJournal:
    def __init__(self, execution_ledger):
        self.ledger = execution_ledger
        self.sessions = execution_ledger.sessions

    def _current(self, session):
        ledger = self.ledger
        run, job = ledger._lock(session)
        if (
            run.status != "RUNNING"
            or job.status != "RUNNING"
            or not job.lease_until
            or job.lease_until <= datetime.now(UTC)
        ):
            raise ValueError("ISOLATED_TOOL_RUN_OR_LEASE_NOT_CURRENT")
        ledger.authorize(session)
        spec = ledger.plan.episode_execution.isolated_tools
        if isinstance(spec, GuardedRecordExecution):
            from fastapi import HTTPException

            from cutback.server.guardrail_controls import current_authority

            try:
                current_authority(
                    session,
                    run.project_id,
                    spec.runtime_guardrails.policy_id,
                    spec.runtime_guardrails,
                )
            except HTTPException:
                raise ValueError("GUARDED_TOOL_CURRENT_AUTHORITY_REQUIRED") from None
        episode = session.get(ExecutionEpisode, ledger.episode_id, with_for_update=True)
        if (
            not episode
            or episode.run_id != run.id
            or episode.project_id != run.project_id
            or episode.state != "running"
        ):
            raise ValueError("ISOLATED_TOOL_RUNNING_EPISODE_REQUIRED")
        return run, job, episode

    def start(self, policy, generation):
        ledger = self.ledger
        spec = ledger.plan.episode_execution.isolated_tools
        if not spec:
            raise ValueError("ISOLATED_TOOL_EXPLICIT_PLAN_REQUIRED")
        with self.sessions.begin() as session:
            run, job, episode = self._current(session)
            environment = session.get(Environment, ledger.environment_id)
            if (
                policy.project_id != run.project_id
                or spec.project_id != run.project_id
                or policy.principal_id != spec.principal_id
                or policy.image_id != spec.image_id
                or not environment
                or environment.name != policy.environment
                or spec.environment != policy.environment
                or policy.workflow != ledger.plan.workflow
                or policy.max_wall_seconds > spec.max_wall_seconds
                or policy.max_calls > ledger.plan.episode_execution.max_tool_calls
                or policy.max_result_bytes != spec.max_result_bytes
                or policy.expires_at > job.lease_until
            ):
                raise ValueError("ISOLATED_TOOL_SCOPE_OR_CAP_OUTSIDE_APPROVED_PLAN")
            project_count = session.scalar(
                select(func.count())
                .select_from(ToolSessionRecord)
                .where(ToolSessionRecord.project_id == run.project_id)
            )
            run_count = session.scalar(
                select(func.count())
                .select_from(ToolSessionRecord)
                .where(ToolSessionRecord.run_id == run.id)
            )
            if project_count >= 10000 or run_count >= spec.max_sessions:
                raise ValueError("ISOLATED_TOOL_SESSION_CAPACITY_EXHAUSTED")
            if session.get(ToolSessionRecord, policy.session_id) or session.scalar(
                select(ToolSessionRecord.id).where(ToolSessionRecord.episode_id == episode.id)
            ):
                raise ValueError("ISOLATED_TOOL_START_ALREADY_RESERVED_NO_RESTART")
            session.add(
                ToolSessionRecord(
                    id=policy.session_id,
                    project_id=run.project_id,
                    environment_id=ledger.environment_id,
                    run_id=run.id,
                    episode_id=episode.id,
                    principal_id=policy.principal_id,
                    broker_id=spec.broker_id,
                    guardrail_policy_id=spec.runtime_guardrails.policy_id
                    if isinstance(spec, GuardedRecordExecution)
                    else None,
                    guardrail_policy_sha256=spec.runtime_guardrails.policy_sha256
                    if isinstance(spec, GuardedRecordExecution)
                    else None,
                    environment_name=policy.environment,
                    workflow=policy.workflow,
                    broker_generation=UUID(str(generation)),
                    policy_sha256=policy.policy_sha256,
                    approval_hash=digest(ledger.plan.model_dump(mode="json")),
                    image_id=policy.image_id,
                    max_calls=policy.max_calls,
                    max_wall_seconds=policy.max_wall_seconds,
                    state="STARTING",
                    calls_used=0,
                    cleanup_confirmed=False,
                    cleanup_requested=False,
                    lease_until=min(
                        policy.expires_at,
                        datetime.now(UTC) + timedelta(seconds=policy.max_wall_seconds),
                    ),
                )
            )

    def _owned(self, session, identifier):
        ledger = self.ledger
        run, _ = ledger._lock(session)
        row = session.get(ToolSessionRecord, identifier, with_for_update=True)
        if (
            not row
            or row.project_id != run.project_id
            or row.run_id != run.id
            or row.environment_id != ledger.environment_id
            or row.episode_id != ledger.episode_id
        ):
            raise ValueError("ISOLATED_TOOL_SESSION_SCOPE_DENIED")
        return row

    def started(self, identifier, receipt):
        with self.sessions.begin() as session:
            self._current(session)
            row = self._owned(session, identifier)
            if (
                row.state != "STARTING"
                or row.lease_until <= datetime.now(UTC)
                or receipt.get("session_id") != str(row.id)
                or receipt.get("policy_sha256") != row.policy_sha256
                or receipt.get("approval_hash") != row.approval_hash
                or receipt.get("state") != "READY"
            ):
                raise ValueError("ISOLATED_TOOL_START_RECEIPT_OR_LEASE_INVALID")
            row.state = "READY"

    def reserve(self, identifier, operation, tool):
        with self.sessions.begin() as session:
            run, _, _ = self._current(session)
            row = self._owned(session, identifier)
            spec = self.ledger.plan.episode_execution.isolated_tools
            total = session.scalar(
                select(func.count())
                .select_from(ToolOperationRecord)
                .where(ToolOperationRecord.run_id == run.id)
            )
            if (
                row.state != "READY"
                or row.lease_until <= datetime.now(UTC)
                or row.calls_used >= row.max_calls
                or total >= spec.max_total_tool_calls
                or session.get(ToolOperationRecord, operation)
            ):
                raise ValueError("ISOLATED_TOOL_OPERATION_CAP_OR_STATE_DENIED_NO_RETRY")
            row.calls_used += 1
            session.add(
                ToolOperationRecord(
                    id=operation,
                    session_id=row.id,
                    project_id=row.project_id,
                    environment_id=row.environment_id,
                    run_id=row.run_id,
                    sequence=row.calls_used,
                    tool_kind=tool if tool in {"records_read", "records_patch"} else "unapproved",
                    state="DISPATCHING",
                    guardrail_policy_id=row.guardrail_policy_id,
                    broker_intent="NOT_STARTED" if row.guardrail_policy_id else None,
                    result_delivery="NOT_RELEASED" if row.guardrail_policy_id else None,
                    state_changed=None,
                )
            )

    def returned(self, identifier, operation, response):
        with self.sessions.begin() as session:
            row = self._owned(session, identifier)
            call = session.get(ToolOperationRecord, operation, with_for_update=True)
            if (
                not call
                or call.session_id != row.id
                or call.state != "DISPATCHING"
                or response.get("operation_id") != str(call.id)
                or response.get("policy_sha256") != row.policy_sha256
                or response.get("status") not in {"returned", "denied"}
                or type(response.get("state_changed")) is not bool
                or call.guardrail_policy_id is not None
                and call.broker_intent != "STARTED"
            ):
                raise ValueError("ISOLATED_TOOL_OPERATION_RECEIPT_INVALID")
            call.state = "RETURNED" if response["status"] == "returned" else "DENIED"
            call.state_changed = response["state_changed"]
            call.completed_at = datetime.now(UTC)

    def broker_started(self, identifier, operation):
        """Commit the one-shot broker intent after the input decision, before I/O."""
        with self.sessions.begin() as session:
            self._current(session)
            row = self._owned(session, identifier)
            call = session.get(ToolOperationRecord, operation, with_for_update=True)
            if (
                call is None
                or call.session_id != row.id
                or call.guardrail_policy_id is None
                or call.guardrail_policy_id != row.guardrail_policy_id
                or call.state != "DISPATCHING"
                or call.broker_intent != "NOT_STARTED"
            ):
                raise ValueError("GUARDED_TOOL_BROKER_INTENT_SCOPE_OR_REPLAY_DENIED")
            self._allowed_guard_decision(session, row, operation, "tool_input")
            call.broker_intent = "STARTED"

    def input_blocked(self, identifier, operation):
        """Known host denial before broker I/O; no fabricated broker receipt."""
        with self.sessions.begin() as session:
            row = self._owned(session, identifier)
            call = session.get(ToolOperationRecord, operation, with_for_update=True)
            if (
                call is None
                or call.session_id != row.id
                or call.guardrail_policy_id is None
                or call.state != "DISPATCHING"
                or call.broker_intent != "NOT_STARTED"
            ):
                raise ValueError("GUARDED_TOOL_PRECALL_DENIAL_NOT_PROVEN")
            call.state, call.result_delivery, call.state_changed = "DENIED", "WITHHELD", False
            call.completed_at = datetime.now(UTC)

    def delivery(self, identifier, operation, *, delivered):
        """A withheld reply cannot erase an independently recorded write effect."""
        with self.sessions.begin() as session:
            if delivered:
                self._current(session)
            row = self._owned(session, identifier)
            call = session.get(ToolOperationRecord, operation, with_for_update=True)
            if (
                type(delivered) is not bool
                or call is None
                or call.session_id != row.id
                or call.guardrail_policy_id is None
                or call.state not in {"RETURNED", "DENIED"}
                or call.broker_intent != "STARTED"
                or call.result_delivery != "NOT_RELEASED"
            ):
                raise ValueError("GUARDED_TOOL_DELIVERY_SCOPE_OR_REPLAY_DENIED")
            if delivered:
                self._allowed_guard_decision(session, row, operation, "tool_output")
            # This is release authorization, not proof that another model or
            # downstream application actually consumed the reply.
            call.result_delivery = "AUTHORIZED" if delivered else "WITHHELD"

    def _allowed_guard_decision(self, session, row, operation, stage):
        from cutback.core.guardrails import GuardrailDecision
        from cutback.server.guardrail_db import GuardrailOperationRow

        record = session.get(
            GuardrailOperationRow, (row.project_id, row.guardrail_policy_id, operation, stage)
        )
        decision = (
            GuardrailDecision.model_validate(record.decision)
            if record and record.decision
            else None
        )
        specification = self.ledger.plan.episode_execution.isolated_tools
        binding = specification.runtime_guardrails
        if (
            decision is None
            or record.state not in {"COMPLETED", "UNKNOWN"}
            or decision.policy_id != row.guardrail_policy_id
            or decision.policy_sha256 != row.guardrail_policy_sha256
            or decision.stage != stage
            or decision.operation_id != operation
            or decision.action != "allow"
            or decision.checks_expected != binding.stage_rule_counts[stage]
            or decision.status == "unavailable"
            and binding.on_unavailable != "fail_open"
        ):
            raise ValueError("GUARDED_TOOL_DURABLE_STAGE_DECISION_REQUIRED")

    def uncertain_start(self, identifier):
        self._uncertain(identifier)

    def uncertain(self, identifier, operation):
        self._uncertain(identifier, operation)

    def uncertain_cleanup(self, identifier):
        self._uncertain(identifier)

    def _uncertain(self, identifier, operation=None):
        with self.sessions.begin() as session:
            row = self._owned(session, identifier)
            if not row.cleanup_confirmed:
                row.state = "UNCERTAIN"
            if operation:
                call = session.get(ToolOperationRecord, operation, with_for_update=True)
                if not call or call.session_id != row.id:
                    raise ValueError("ISOLATED_TOOL_OPERATION_SCOPE_DENIED")
                # A lost persistence acknowledgement cannot erase a committed
                # receipt, nor invent the missing result for the next model turn.
                if call.state == "DISPATCHING":
                    call.state = "UNCERTAIN"
                    if call.guardrail_policy_id is not None:
                        call.broker_intent, call.result_delivery = "UNKNOWN", "UNKNOWN"

    def closed(self, identifier, receipt):
        with self.sessions.begin() as session:
            row = self._owned(session, identifier)
            if (
                receipt.get("session_id") != str(row.id)
                or receipt.get("policy_sha256") != row.policy_sha256
                or receipt.get("cleanup_confirmed") is not True
                or receipt.get("state") != "CLOSED"
            ):
                raise ValueError("ISOLATED_TOOL_CONTAINMENT_RECEIPT_INVALID")
            row.cleanup_confirmed, row.state, row.completed_at = True, "CLOSED", datetime.now(UTC)
            row.cleanup_requested = False
