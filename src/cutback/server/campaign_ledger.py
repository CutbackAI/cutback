"""Parent/child accounting inside the existing project-locked native ledger transaction."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from cutback.server.campaign_db import CampaignRecord, CampaignRun
from cutback.server.principals import principal_role

ACTIVE = {"RUNNING", "WAITING_PROPOSAL", "WAITING_VALIDATION", "WAITING_CONFIRMATION"}


def campaign_for_run(session, run):
    link = session.scalar(select(CampaignRun).where(CampaignRun.run_id == run.id))
    if link is None:
        return None, None
    parent = session.get(CampaignRecord, link.campaign_id, with_for_update=True)
    if not parent or (link.project_id, parent.project_id, link.environment_id) != (
        run.project_id,
        run.project_id,
        parent.environment_id,
    ):
        raise ValueError("CAMPAIGN_RUN_SCOPE_MISMATCH")
    return parent, link


def reserve_campaign_call(
    session, run, environment, plan_sha256, purpose, amount, *, operation=None
):
    parent, link = campaign_for_run(session, run)
    if operation == "source-policy-generation-v1":
        if parent is not None or purpose != "optimization":
            raise ValueError("POLICY_GENERATION_REQUIRES_SEPARATE_APPROVED_PARENT")
        from cutback.server.policy_program_service import program_for_run

        if program_for_run(session, run)[0] is None:
            raise ValueError("POLICY_GENERATION_REQUIRES_APPROVED_PARENT")
        return
    if operation == "source-policy-training-v1":
        if parent is not None or purpose != "optimization":
            raise ValueError("POLICY_TRAINING_REQUIRES_SEPARATE_APPROVED_RUN")
        from cutback.server.policy_service import reserve_training

        reserve_training(session, run, environment, plan_sha256)
        return
    if operation == "scenario-generation-v1":
        if parent is not None or purpose != "optimization":
            raise ValueError("SCENARIO_GENERATION_REQUIRES_SEPARATE_APPROVED_RUN")
        from cutback.server.scenario_service import reserve_scenario_generation

        reserve_scenario_generation(session, run, environment, plan_sha256, amount)
        return
    if operation == "routing-training-v1":
        if parent is not None or purpose != "optimization":
            raise ValueError("ROUTING_TRAINING_REQUIRES_SEPARATE_APPROVED_RUN")
        from cutback.server.routing_service import reserve_training

        reserve_training(session, run, environment, plan_sha256)
        return
    if parent is None:
        if purpose == "optimization":
            if operation not in {"criteria-generation-v1", "criteria-generation-v2"}:
                raise ValueError("PROPOSER_REQUIRES_APPROVED_CAMPAIGN")
            from cutback.server.criteria_service import reserve_criteria_generation

            reserve_criteria_generation(session, run, environment, plan_sha256, amount)
        return
    now = datetime.now(UTC)
    if (
        parent.approval_status != "consumed"
        or parent.status not in ACTIVE
        or parent.expires_at <= now
        or parent.deadline_at is None
        or parent.deadline_at <= now
    ):
        raise ValueError("CAMPAIGN_CANCELLED_EXPIRED_OR_NOT_ACTIVE")
    if (
        link.environment_id != environment
        or link.plan_sha256 != plan_sha256
        or link.campaign_sha256 != parent.approval_sha256
    ):
        raise ValueError("CAMPAIGN_FROZEN_SCOPE_MISMATCH")
    if (purpose == "optimization") != (link.phase == "proposal"):
        raise ValueError("CAMPAIGN_REQUEST_PHASE_MISMATCH")
    if principal_role(session, run.project_id, parent.approved_by or "") not in {
        "admin",
        "project_admin",
    }:
        raise ValueError("CAMPAIGN_APPROVING_PRINCIPAL_NO_LONGER_AUTHORIZED")
    envelope = parent.economic_envelope
    try:
        amount_cap = Decimal(str(envelope["effective_max_usd"]))
        requests_cap = envelope["max_requests"]
        owner_budget = parent.specification["budget"]
        owner_usd = Decimal(str(owner_budget["max_usd"]))
        owner_requests = owner_budget["max_requests"]
        if (
            envelope.get("eligible") is not True
            or not amount_cap.is_finite()
            or amount_cap < 0
            or type(requests_cap) is not int
            or not 1 <= requests_cap <= 10000
            or not owner_usd.is_finite()
            or not 0 <= owner_usd <= 10000
            or type(owner_requests) is not int
            or not 1 <= owner_requests <= 10000
            or amount_cap > owner_usd
            or requests_cap > owner_requests
        ):
            raise ValueError("CAMPAIGN_ECONOMIC_ENVELOPE_NOT_ELIGIBLE")
    except (KeyError, TypeError, InvalidOperation):
        raise ValueError("CAMPAIGN_ECONOMIC_ENVELOPE_NOT_ELIGIBLE") from None
    if parent.requests_used >= requests_cap or parent.reserved_usd + amount > amount_cap:
        raise ValueError("CAMPAIGN_BUDGET_EXHAUSTED")
    parent.requests_used += 1
    parent.reserved_usd += amount


def record_campaign_cost(session, run, spent):
    """Called only on the first unknown-to-known transition of a physical call.

    Expiry, revoked authority or cancellation cannot erase a late billed result.
    No status transition or new request is authorized by this accounting repair.
    """
    parent, _ = campaign_for_run(session, run)
    if parent is not None:
        parent.spent_usd += spent
        parent.known_cost_requests += 1
