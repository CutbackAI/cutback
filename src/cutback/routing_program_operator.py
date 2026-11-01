"""Scoped local continuation; it never approves, resets or replays a billable child."""

import math
import re
import time
from decimal import Context, localcontext
from uuid import UUID

from sqlalchemy import select

from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_ledger import recover_execution_leases
from cutback.server.routing_program_db import RoutingProgramRecord, RoutingProgramRun
from cutback.server.routing_program_retention import expire_programs
from cutback.server.routing_program_service import ACTIVE, PHASE_STATUS
from cutback.server.routing_program_worker import advance_one
from cutback.server.worker import run_one

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "EXPIRED", "ABSTAINED"}


class RoutingProgramOperator:
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
            raise ValueError("ROUTING_PROGRAM_OPERATOR_EXACT_SCOPE_REQUIRED")
        self.sessions, self.store, self.program_hash = sessions, store, program_hash
        self.preflight, self.executor = preflight, executor

    def snapshot(self):
        with self.sessions() as session:
            row = session.get(RoutingProgramRecord, self.program_id)
            if (
                not row
                or row.project_id != self.project_id
                or row.approval_sha256 != self.program_hash
            ):
                raise ValueError("ROUTING_PROGRAM_OPERATOR_SCOPE_OR_HASH_CHANGED")
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
                    RoutingProgramRun.run_id, RoutingProgramRun.phase, ExperimentRunRecord.status
                )
                .join(ExperimentRunRecord, ExperimentRunRecord.id == RoutingProgramRun.run_id)
                .where(
                    RoutingProgramRun.program_id == self.program_id,
                    RoutingProgramRun.project_id == self.project_id,
                    ExperimentRunRecord.project_id == self.project_id,
                )
                .limit(5)
            ).all()
            if len(rows) > 4:
                raise ValueError("ROUTING_PROGRAM_OPERATOR_CHILD_BOUND_EXCEEDED")
            return rows

    def recover(self):
        self.snapshot()
        for child in self.children():
            recover_execution_leases(self.sessions, only_run_id=child.run_id)
        expire_programs(self.sessions, only_program_id=self.program_id)
        return self.snapshot()

    def step(self, *, execute_child=True):
        with localcontext(Context(prec=80)):
            current = self.recover()
            if current["status"] in TERMINAL:
                return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
            if current["status"] not in ACTIVE or current["approval_status"] != "consumed":
                return {**current, "child_attempted": False, "operator_code": "APPROVAL_REQUIRED"}
            if self.preflight:
                self.preflight()
            advance_one(
                self.sessions,
                self.store,
                execution_enabled=True,
                only_program_id=self.program_id,
                synthetic_transport=getattr(self.executor, "synthetic_transport", False) is True,
            )
            current = self.snapshot()
            if current["status"] in TERMINAL:
                return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
            ready = [row for row in self.children() if row.status in {"QUEUED", "BLOCKED"}]
            if len(ready) > 1:
                raise ValueError("ROUTING_PROGRAM_MULTIPLE_ACTIVE_CHILDREN")
            if not ready:
                return {**current, "child_attempted": False, "operator_code": "WAITING"}
            if not execute_child:
                return {
                    **current,
                    "child_attempted": False,
                    "operator_code": "LOCAL_OPERATION_BOUND_REACHED",
                }
            child = ready[0]
            if current["status"] != PHASE_STATUS[child.phase]:
                raise ValueError("ROUTING_PROGRAM_OPERATOR_ACTIVE_PHASE_CHANGED")
            if self.preflight:
                self.preflight()
            result = run_one(
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

    def run(self, *, max_operations=4, max_wait_seconds=300, poll_seconds=1, emit=None):
        if type(max_operations) is not int or not 1 <= max_operations <= 4:
            raise ValueError("ROUTING_PROGRAM_OPERATOR_OPERATION_BOUND_REQUIRED")
        if (
            type(max_wait_seconds) is not int
            or not 1 <= max_wait_seconds <= 3600
            or type(poll_seconds) not in {int, float}
            or not math.isfinite(poll_seconds)
            or not 0.1 <= poll_seconds <= 5
        ):
            raise ValueError("ROUTING_PROGRAM_OPERATOR_WAIT_BOUND_REQUIRED")
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
                "NO_EXPERIMENT_API_KEY_OR_SUPPORTED_EXTRA",
            }:
                return result
            remaining = stop - time.monotonic()
            if remaining <= 0:
                return {**result, "operator_code": "WAIT_BOUND_REACHED_RESUME_SAME_PROGRAM"}
            time.sleep(min(remaining, poll_seconds))


def run_local(root, *, max_operations=4, max_wait_seconds=300, emit=None):
    import base64
    import os

    from cutback.agent_control import connect
    from cutback.agent_routing_program import current, status
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.local_stack import configured, database_url
    from cutback.server.db import database

    control, _ = connect(root)
    saved, receipt = current(root, control), status(root, control)
    location, config = configured(root)
    if saved["endpoint"] != config["endpoint"] or not config["allow_payloads"]:
        raise ValueError("ROUTING_PROGRAM_LOCAL_OPERATOR_DESTINATION_MISMATCH")

    def preflight():
        fresh = current(root, control)
        if fresh != saved or status(root, control)["program_hash"] != receipt["program_hash"]:
            raise ValueError("ROUTING_PROGRAM_LOCAL_CHECKPOINT_CHANGED")

    engine, sessions = database(database_url(location, config))
    old = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    try:
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
        store = EncryptedFileStore(
            location / "encrypted", base64.b64decode(config["payload_key"], validate=True)
        )
        runner = RoutingProgramOperator(
            sessions,
            store,
            project_id=saved["project_id"],
            program_id=saved["program_id"],
            program_hash=receipt["program_hash"],
            preflight=preflight,
        )
        return runner.run(
            max_operations=max_operations, max_wait_seconds=max_wait_seconds, emit=emit
        )
    finally:
        engine.dispose()
        if old is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = old
