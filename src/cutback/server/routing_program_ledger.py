"""Routing parent accounting shares the canonical project-locked transaction."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from cutback.server.campaign_db import CampaignRun
from cutback.server.principals import principal_role
from cutback.server.routing_program_service import PHASE_STATUS, program_for_run


def reserve_program_call(
    session, run, environment, plan_sha256, purpose, amount, *, operation=None
):
    parent, link = program_for_run(session, run)
    if parent is None:
        return
    if session.scalar(select(CampaignRun.id).where(CampaignRun.run_id == run.id)):
        raise ValueError("EXECUTION_MULTIPLE_PARENT_OWNERS_NOT_QUALIFIED")
    now = datetime.now(UTC)
    if (
        parent.approval_status != "consumed"
        or parent.status != PHASE_STATUS[link.phase]
        or parent.expires_at <= now
        or parent.deadline_at is None
        or parent.deadline_at <= now
        or principal_role(session, run.project_id, parent.approved_by or "")
        not in {"admin", "project_admin"}
    ):
        raise ValueError("ROUTING_PROGRAM_CANCELLED_EXPIRED_OR_AUTHORITY_REVOKED")
    if link.environment_id != environment or link.plan_sha256 != plan_sha256:
        raise ValueError("ROUTING_PROGRAM_CALL_FROZEN_SCOPE_CHANGED")
    training = link.phase == "training"
    if purpose != ("optimization" if training else "evaluation") or (
        operation != ("routing-training-v1" if training else None)
    ):
        raise ValueError("ROUTING_PROGRAM_REQUEST_PHASE_MISMATCH")
    envelope = parent.economic_envelope
    try:
        cap = Decimal(str(envelope["effective_max_usd"]))
        requests = envelope["max_requests"]
        owner = parent.specification["budget"]
        owner_usd = Decimal(str(owner["max_usd"]))
        owner_requests = owner["max_requests"]
        if (
            envelope.get("contract") != "cutback.routing-program-native-budget.v1"
            or envelope.get("eligible") is not True
            or not cap.is_finite()
            or not owner_usd.is_finite()
            or not 0 <= cap <= owner_usd <= 10000
            or type(requests) is not int
            or type(owner_requests) is not int
            or not 1 <= requests <= owner_requests <= 10000
        ):
            raise ValueError("ROUTING_PROGRAM_ECONOMIC_ENVELOPE_NOT_ELIGIBLE")
    except (KeyError, TypeError, InvalidOperation):
        raise ValueError("ROUTING_PROGRAM_ECONOMIC_ENVELOPE_NOT_ELIGIBLE") from None
    if parent.requests_used >= requests or parent.reserved_usd + amount > cap:
        raise ValueError("ROUTING_PROGRAM_BUDGET_EXHAUSTED")
    parent.requests_used += 1
    parent.reserved_usd += amount


def record_program_cost(session, run, spent):
    # Unknown-to-known only, including a late billed reply after cancellation.
    # Updating accounting never queues work or changes the parent's status.
    parent, _ = program_for_run(session, run)
    if parent is not None:
        parent.spent_usd += spent
        parent.known_cost_requests += 1
