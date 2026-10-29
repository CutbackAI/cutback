"""Drive one already approved campaign; never approve, reset a budget or replay a call.

This is an optional local operator boundary, not part of offline source scanning.
The CLI supplies a fresh source/destination preflight. Independent examples inject
owned native transports into this same driver rather than fabricate run results.
"""

import math
import re
import time
from datetime import UTC, datetime
from decimal import Context, localcontext
from uuid import UUID

from sqlalchemy import select

from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.campaign_retention import expire_campaigns
from cutback.server.campaign_worker import advance_one
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_ledger import recover_execution_leases
from cutback.server.proposal_worker import run_proposal_one
from cutback.server.worker import run_one

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "ABSTAINED"}
APPROVED = {"QUEUED", "RUNNING", "WAITING_PROPOSAL", "WAITING_VALIDATION", "WAITING_CONFIRMATION"}


class CampaignOperator:
    def __init__(
        self,
        sessions,
        store,
        *,
        project_id,
        campaign_id,
        campaign_hash,
        preflight=None,
        executor=None,
    ):
        self.project_id, self.campaign_id = UUID(str(project_id)), UUID(str(campaign_id))
        if (
            not self.project_id.int
            or not self.campaign_id.int
            or not isinstance(campaign_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", campaign_hash)
        ):
            raise ValueError("EXACT_CAMPAIGN_OPERATOR_SCOPE_REQUIRED")
        self.sessions, self.store = sessions, store
        self.campaign_hash = campaign_hash
        self.preflight, self.executor = preflight, executor

    def snapshot(self):
        with self.sessions() as session:
            row = session.scalar(
                select(CampaignRecord).where(
                    CampaignRecord.id == self.campaign_id,
                    CampaignRecord.project_id == self.project_id,
                )
            )
            if row is None or row.approval_sha256 != self.campaign_hash:
                raise ValueError("CAMPAIGN_OPERATOR_SCOPE_OR_APPROVAL_CHANGED")
            decision = row.summary.get("decision")
            return {
                "campaign_id": str(row.id),
                "campaign_hash": row.approval_sha256,
                "status": row.status,
                "approval_status": row.approval_status,
                "cumulative_requests": row.requests_used,
                "cumulative_known_cost_requests": row.known_cost_requests,
                "cumulative_reserved_usd": str(row.reserved_usd),
                "cumulative_known_spend_subtotal_usd": str(row.spent_usd),
                "deadline_at": row.deadline_at.isoformat() if row.deadline_at else None,
                "decision": decision
                if isinstance(decision, str)
                and decision
                in {
                    "prepare_application_checks",
                    "synthetic_confirmation_passed_real_data_required",
                    "reject_no_quality_preserving_lower_cost_candidate",
                    "confirmation_requires_review_or_rejected",
                }
                else None,
                "deployment_authorized": False,
            }

    def children(self):
        with self.sessions() as session:
            rows = session.execute(
                select(
                    CampaignRun.run_id,
                    CampaignRun.phase,
                    ExperimentRunRecord.status,
                )
                .join(ExperimentRunRecord, ExperimentRunRecord.id == CampaignRun.run_id)
                .where(
                    CampaignRun.campaign_id == self.campaign_id,
                    CampaignRun.project_id == self.project_id,
                    ExperimentRunRecord.project_id == self.project_id,
                )
                .order_by(CampaignRun.created_at, CampaignRun.id)
                .limit(9)
            ).all()
            if len(rows) > 8:
                raise ValueError("CAMPAIGN_OPERATOR_CHILD_BOUND_EXCEEDED")
            return rows

    def recover(self):
        self.snapshot()  # Validate exact project/hash before any scoped metadata recovery.
        # Recovery is metadata-only and scoped, even without provider credentials.
        for child in self.children():
            recover_execution_leases(self.sessions, only_run_id=child.run_id)
        expire_campaigns(self.sessions, only_campaign_id=self.campaign_id)
        return self.snapshot()

    def step(self, *, execute_child=True):
        # Embedded Python callers may change precision, rounding and signal traps.
        # Preserve them outside this action while keeping native ledger arithmetic exact.
        with localcontext(Context(prec=80)):
            return self._step(execute_child=execute_child)

    def _step(self, *, execute_child=True):
        current = self.recover()
        if current["status"] in TERMINAL:
            return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
        if current["status"] not in APPROVED or current["approval_status"] != "consumed":
            return {**current, "child_attempted": False, "operator_code": "APPROVAL_REQUIRED"}
        if self.preflight is not None:
            self.preflight()
        advance_one(
            self.sessions,
            self.store,
            execution_enabled=True,
            only_campaign_id=self.campaign_id,
            synthetic_transport=getattr(self.executor, "synthetic_transport", False) is True,
        )
        current = self.snapshot()
        if current["status"] in TERMINAL:
            return {**current, "child_attempted": False, "operator_code": "TERMINAL"}
        ready = [c for c in self.children() if c.status in {"QUEUED", "BLOCKED"}]
        if len(ready) > 1:
            raise ValueError("CAMPAIGN_OPERATOR_MULTIPLE_ACTIVE_CHILDREN")
        if not ready:
            return {**current, "child_attempted": False, "operator_code": "WAITING"}
        if not execute_child:
            return {
                **current,
                "child_attempted": False,
                "operator_code": "LOCAL_OPERATION_BOUND_REACHED",
            }
        child = ready[0]
        if current["status"] != "WAITING_" + child.phase.upper():
            raise ValueError("CAMPAIGN_OPERATOR_ACTIVE_PHASE_MISMATCH")
        if self.preflight is not None:
            self.preflight()
        worker = run_proposal_one if child.phase == "proposal" else run_one
        result = worker(
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

    def run(self, *, max_operations=8, poll_seconds=1.0, emit=None):
        if type(max_operations) is not int or not 1 <= max_operations <= 8:
            raise ValueError("CAMPAIGN_OPERATOR_OPERATION_BOUND_REQUIRED")
        if (
            type(poll_seconds) not in {int, float}
            or not math.isfinite(poll_seconds)
            or not 0.1 <= poll_seconds <= 5
        ):
            raise ValueError("CAMPAIGN_OPERATOR_POLL_BOUND_REQUIRED")
        initial = self.snapshot()
        if emit is not None:
            emit(
                {
                    **initial,
                    "operator_code": "STARTING_LOCAL_OPERATOR",
                    "local_child_attempts": 0,
                    "child_attempted": False,
                }
            )
        wait = (
            (datetime.fromisoformat(initial["deadline_at"]) - datetime.now(UTC)).total_seconds()
            if initial["deadline_at"]
            else 3600
        )
        stop = time.monotonic() + min(3600, max(0, wait))
        attempts, previous, first = 0, None, True
        while True:
            if not first and time.monotonic() >= stop:
                return {
                    **self.recover(),
                    "child_attempted": False,
                    "operator_code": "LOCAL_WAIT_BOUND_REACHED",
                    "local_child_attempts": attempts,
                }
            result = self.step(execute_child=attempts < max_operations)
            first = False
            attempts += int(result["child_attempted"])
            result["local_child_attempts"] = attempts
            identity = (result["status"], result["operator_code"], result["cumulative_requests"])
            if emit is not None and identity != previous:
                emit(result)
            previous = identity
            if result["status"] in TERMINAL or result["operator_code"] == "APPROVAL_REQUIRED":
                return result
            if result["operator_code"] in {
                "NO_EXPERIMENT_API_KEY_OR_SUPPORTED_EXTRA",
                "NO_PROPOSER_API_KEY_OR_SUPPORTED_EXTRA",
            }:
                return result
            if result["operator_code"] == "LOCAL_OPERATION_BOUND_REACHED":
                return result
            time.sleep(poll_seconds)


def run_local(root, *, complete=False, max_operations=8, emit=None):
    """Use only the saved owned local stack, scoped API and unchanged source profile."""
    import base64
    import os

    from cutback.agent import load_session, selected_profile, workspace
    from cutback.agent_campaign import current, status
    from cutback.agent_control import connect
    from cutback.core.workspace import workspace_lock
    from cutback.evals.artifacts import EncryptedFileStore
    from cutback.local_stack import configured, database_url
    from cutback.server.db import database

    control, _ = connect(root)
    saved = current(root, control)
    location, config = configured(root)
    if saved["endpoint"] != config["endpoint"] or not config["allow_payloads"]:
        raise ValueError("LOCAL_OPERATOR_SCOPE_MISMATCH")
    receipt = status(root, control)
    expected = (saved["project_id"], saved["campaign_id"], saved["workflow"], saved["endpoint"])

    def preflight():
        _, directory = workspace(root)
        with workspace_lock(directory):
            fresh = current(root, control)
            scope = (
                fresh["project_id"],
                fresh["campaign_id"],
                fresh["workflow"],
                fresh["endpoint"],
            )
            if scope != expected:
                raise ValueError("LOCAL_OPERATOR_CHECKPOINT_SCOPE_CHANGED")
            selected_profile(root, load_session(root), fresh["workflow"])
            if status(root, control)["campaign_hash"] != receipt["campaign_hash"]:
                raise ValueError("CAMPAIGN_OPERATOR_SCOPE_OR_APPROVAL_CHANGED")

    engine, sessions = database(database_url(location, config))
    old = os.environ.get("CUTBACK_ALLOW_PAYLOADS")
    try:
        os.environ["CUTBACK_ALLOW_PAYLOADS"] = "true"
        store = EncryptedFileStore(
            location / "encrypted", base64.b64decode(config["payload_key"], validate=True)
        )
        runner = CampaignOperator(
            sessions,
            store,
            project_id=saved["project_id"],
            campaign_id=saved["campaign_id"],
            campaign_hash=receipt["campaign_hash"],
            preflight=preflight,
        )
        return runner.run(max_operations=max_operations, emit=emit) if complete else runner.step()
    finally:
        engine.dispose()
        if old is None:
            os.environ.pop("CUTBACK_ALLOW_PAYLOADS", None)
        else:
            os.environ["CUTBACK_ALLOW_PAYLOADS"] = old
