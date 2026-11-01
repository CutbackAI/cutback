"""Bounded local control of an existing approval, never a billable replay mechanism."""

import math
import re
import time
from decimal import Context, localcontext
from uuid import UUID

from sqlalchemy import select

from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_ledger import recover_execution_leases
from cutback.server.policy_generation_worker import run_generation_one
from cutback.server.policy_program_db import SourcePolicyProgramRecord, SourcePolicyProgramRun
from cutback.server.policy_program_retention import expire_programs
from cutback.server.policy_program_service import ACTIVE, PHASE_STATUS
from cutback.server.policy_program_worker import advance_one
from cutback.server.worker import run_one

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"}


class SourcePolicyProgramOperator:
    def __init__(
        self,
        sessions,
        store,
        *,
        project_id,
        program_id,
        program_hash,
        preflight=None,
        executor=None,
    ):
        self.project_id, self.program_id = UUID(str(project_id)), UUID(str(program_id))
        if (
            not self.project_id.int
            or not self.program_id.int
            or not re.fullmatch(r"[a-f0-9]{64}", program_hash)
        ):
            raise ValueError("POLICY_PROGRAM_OPERATOR_EXACT_SCOPE_REQUIRED")
        self.sessions, self.store, self.program_hash = sessions, store, program_hash
        self.preflight, self.executor = preflight, executor

    def snapshot(self):
        with self.sessions() as session:
            row = session.get(SourcePolicyProgramRecord, self.program_id)
            if (
                not row
                or row.project_id != self.project_id
                or row.approval_sha256 != self.program_hash
            ):
                raise ValueError("POLICY_PROGRAM_OPERATOR_SCOPE_OR_HASH_CHANGED")
            return {
                "program_id": str(row.id),
                "program_hash": row.approval_sha256,
                "status": row.status,
                "approval_status": row.approval_status,
                "requests": row.requests_used,
                "known_cost_requests": row.known_cost_requests,
                "reserved_usd": str(row.reserved_usd),
                "known_spend_subtotal_usd": str(row.spent_usd),
                "deployment_authorized": False,
            }

    def children(self):
        with self.sessions() as session:
            rows = session.execute(
                select(
                    SourcePolicyProgramRun.run_id,
                    SourcePolicyProgramRun.phase,
                    ExperimentRunRecord.status,
                )
                .join(ExperimentRunRecord, ExperimentRunRecord.id == SourcePolicyProgramRun.run_id)
                .where(
                    SourcePolicyProgramRun.program_id == self.program_id,
                    SourcePolicyProgramRun.project_id == self.project_id,
                    ExperimentRunRecord.project_id == self.project_id,
                )
                .limit(11)
            ).all()
            if len(rows) > 10:
                raise ValueError("POLICY_PROGRAM_OPERATOR_CHILD_BOUND_EXCEEDED")
            return rows

    def step(self, *, execute_child=True):
        with localcontext(Context(prec=80)):
            self.snapshot()
            for child in self.children():
                recover_execution_leases(self.sessions, only_run_id=child.run_id)
            expire_programs(self.sessions, only_program_id=self.program_id)
            current = self.snapshot()
            if current["status"] in TERMINAL:
                return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
            if current["status"] not in ACTIVE or current["approval_status"] != "consumed":
                return {**current, "child_attempted": False, "operator_code": "APPROVAL_REQUIRED"}
            if self.preflight:
                self.preflight()
            transition = advance_one(
                self.sessions,
                self.store,
                execution_enabled=True,
                only_program_id=self.program_id,
                synthetic_transport=getattr(self.executor, "synthetic_transport", False) is True,
            )
            current = self.snapshot()
            if transition["status"] == "SYNTHETIC_EXECUTOR_REQUIRED":
                return {
                    **current,
                    "child_attempted": False,
                    "operator_code": "SYNTHETIC_EXECUTOR_REQUIRED",
                }
            if current["status"] in TERMINAL:
                return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
            ready = [row for row in self.children() if row.status in {"QUEUED", "BLOCKED"}]
            if len(ready) > 1:
                raise ValueError("POLICY_PROGRAM_MULTIPLE_ACTIVE_CHILDREN")
            if not ready:
                return {**current, "child_attempted": False, "operator_code": "WAITING"}
            child = ready[0]
            if child.status == "BLOCKED":
                return {
                    **current,
                    "child_attempted": False,
                    "operator_code": "BLOCKED_CHILD_REQUIRES_EXPLICIT_OPERATOR_REVIEW",
                }
            if not execute_child:
                return {
                    **current,
                    "child_attempted": False,
                    "operator_code": "LOCAL_OPERATION_BOUND_REACHED",
                }
            if current["status"] != PHASE_STATUS[child.phase]:
                raise ValueError("POLICY_PROGRAM_OPERATOR_ACTIVE_PHASE_CHANGED")
            if self.preflight:
                self.preflight()
            execute = run_generation_one if child.phase == "generation" else run_one
            result = execute(
                self.sessions,
                self.store,
                executor=self.executor,
                execution_enabled=True,
                only_run_id=child.run_id,
            )
            return {
                **self.snapshot(),
                "child_attempted": True,
                "child_run_id": str(child.run_id),
                "child_phase": child.phase,
                "child_status": result.get("status"),
                "operator_code": result.get("status", "CHILD_STATUS_UNKNOWN"),
            }

    def run(self, *, max_operations=10, max_wait_seconds=300, poll_seconds=1, emit=None):
        if (
            type(max_operations) is not int
            or not 1 <= max_operations <= 10
            or type(max_wait_seconds) is not int
            or not 1 <= max_wait_seconds <= 3600
            or type(poll_seconds) not in {int, float}
            or not math.isfinite(poll_seconds)
            or not 0.1 <= poll_seconds <= 5
        ):
            raise ValueError("POLICY_PROGRAM_OPERATOR_OPERATION_AND_WAIT_BOUNDS_REQUIRED")
        attempts, stop = 0, time.monotonic() + max_wait_seconds
        while True:
            result = self.step(execute_child=attempts < max_operations)
            attempts += int(result["child_attempted"])
            result["local_child_attempts"] = attempts
            if emit:
                emit(result)
            if result["status"] in TERMINAL or result["operator_code"] in {
                "APPROVAL_REQUIRED",
                "LOCAL_OPERATION_BOUND_REACHED",
                "SYNTHETIC_EXECUTOR_REQUIRED",
                "BLOCKED_CHILD_REQUIRES_EXPLICIT_OPERATOR_REVIEW",
                "NO_EXPERIMENT_API_KEY_OR_SUPPORTED_EXTRA",
                "NO_GENERATOR_API_KEY_OR_SUPPORTED_EXTRA",
                "BLOCKED",
            }:
                return result
            remaining = stop - time.monotonic()
            if remaining <= 0:
                return {**result, "operator_code": "WAIT_BOUND_REACHED_RESUME_SAME_PROGRAM"}
            time.sleep(min(remaining, poll_seconds))


def run_local(root, *, max_operations=10, max_wait_seconds=300, emit=None):
    import base64
    import os

    from cutback.agent_control import connect
    from cutback.agent_policy_program import current, status
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.local_stack import configured, database_url
    from cutback.server.db import database

    control, _ = connect(root)
    saved, receipt = current(root, control), status(root, control)
    location, config = configured(root)
    if saved["endpoint"] != config["endpoint"] or not config["allow_payloads"]:
        raise ValueError("POLICY_PROGRAM_LOCAL_OPERATOR_DESTINATION_MISMATCH")

    def preflight():
        if (
            current(root, control) != saved
            or status(root, control)["program_hash"] != receipt["program_hash"]
        ):
            raise ValueError("POLICY_PROGRAM_LOCAL_CHECKPOINT_CHANGED")

    engine, sessions = database(database_url(location, config))
    previous = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    try:
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
        store = EncryptedFileStore(
            location / "encrypted", base64.b64decode(config["payload_key"], validate=True)
        )
        return SourcePolicyProgramOperator(
            sessions,
            store,
            project_id=saved["project_id"],
            program_id=saved["program_id"],
            program_hash=receipt["program_hash"],
            preflight=preflight,
        ).run(max_operations=max_operations, max_wait_seconds=max_wait_seconds, emit=emit)
    finally:
        engine.dispose()
        if previous is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = previous
