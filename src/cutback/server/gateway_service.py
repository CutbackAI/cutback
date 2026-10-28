"""Durable gateway approval and one-shot accounting. No payload storage or HTTP."""

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from functools import wraps
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, select

from cutback.gateway.contracts import (
    GatewayKeyInput,
    GatewayPolicy,
    ModelLimit,
    ResponsesText,
    qualify_model,
    qualify_request,
    upper_cost,
)
from cutback.server.dataset_lineage import lock_lineage
from cutback.server.db import AuditEvent, Environment, Project, new_token, token_hash
from cutback.server.gateway_db import (
    GatewayBudgetRow,
    GatewayCallRow,
    GatewayKeyRow,
    GatewayPolicyRow,
)
from cutback.server.principals import principal_role


class GatewayError(Exception):
    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code, self.status = code, status


def exact_accounting(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with localcontext() as context:
            context.prec = 80
            return function(*args, **kwargs)

    return wrapped


def lock_project(session, project_id):
    lock_lineage(session, project_id)
    if session.get(Project, project_id, with_for_update=True) is None:
        raise GatewayError("GATEWAY_PROJECT_NOT_FOUND", 404)


def audit(session, project, actor, action, **details):
    session.add(AuditEvent(project_id=project, actor=actor, action=action, details=details))


def current_admin(session, project, actor):
    if principal_role(session, project, actor or "") not in {"admin", "project_admin"}:
        raise GatewayError("GATEWAY_CURRENT_ADMIN_REQUIRED", 403)


def policy_spec(row):
    specification = GatewayPolicy.model_validate(row.specification)
    if not hmac.compare_digest(specification.approval_hash(), row.approval_hash):
        raise GatewayError("GATEWAY_POLICY_INTEGRITY_FAILURE", 409)
    return specification


def create_policy(session, project, specification: GatewayPolicy, actor, *, now=None):
    lock_project(session, project)
    now = now or datetime.now(UTC)
    current_admin(session, project, actor)
    prior = session.scalar(
        select(GatewayPolicyRow).where(
            GatewayPolicyRow.project_id == project,
            GatewayPolicyRow.policy_id == specification.policy_id,
        )
    )
    if prior:
        if prior.approval_hash != specification.approval_hash():
            raise GatewayError("GATEWAY_POLICY_ID_CONFLICT", 409)
        return prior
    if (
        specification.starts_at < now - timedelta(minutes=5)
        or specification.expires_at <= now
        or specification.expires_at > now + timedelta(days=31)
    ):
        raise GatewayError("GATEWAY_POLICY_WINDOW_OUTSIDE_APPROVAL_HORIZON", 422)
    found = set(
        session.scalars(
            select(Environment.id).where(
                Environment.project_id == project, Environment.id.in_(specification.environment_ids)
            )
        ).all()
    )
    if found != set(specification.environment_ids):
        raise GatewayError("GATEWAY_ENVIRONMENT_SCOPE_DENIED", 403)
    for model in specification.models:
        qualify_model(model)
        if upper_cost(model) > min(
            model.limit.modeled_usd, specification.project_limit.modeled_usd
        ):
            raise GatewayError("GATEWAY_MODEL_LIMIT_BELOW_ONE_FULL_RESERVATION", 422)
    if (
        session.scalar(
            select(func.count())
            .select_from(GatewayPolicyRow)
            .where(GatewayPolicyRow.project_id == project)
        )
        >= 256
    ):
        raise GatewayError("GATEWAY_POLICY_CAPACITY_REACHED", 429)
    row = GatewayPolicyRow(
        project_id=project,
        policy_id=specification.policy_id,
        specification=specification.model_dump(mode="json"),
        approval_hash=specification.approval_hash(),
        starts_at=specification.starts_at,
        expires_at=specification.expires_at,
    )
    session.add(row)
    session.flush()
    audit(session, project, actor, "gateway_policy_created", policy_id=str(row.id))
    return row


def approve_policy(session, project, row_id, approval_hash, approval_ref, actor, *, now=None):
    lock_project(session, project)
    now = now or datetime.now(UTC)
    current_admin(session, project, actor)
    row = session.get(GatewayPolicyRow, row_id)
    if row is None or row.project_id != project:
        raise GatewayError("GATEWAY_POLICY_NOT_FOUND", 404)
    specification = policy_spec(row)
    if not hmac.compare_digest(row.approval_hash, approval_hash):
        raise GatewayError("GATEWAY_EXACT_APPROVAL_HASH_REQUIRED", 409)
    if (
        row.state == "APPROVED"
        and row.approved_by == actor
        and row.approval_ref == approval_ref
        and row.expires_at > now
    ):
        return row
    if row.state != "PENDING" or not now < row.expires_at <= now + timedelta(days=31):
        raise GatewayError("GATEWAY_POLICY_NOT_APPROVABLE", 409)
    # Disabled policies still occupy their original window: replacing a policy
    # must not reset a shared project budget after money was reserved or spent.
    overlap = session.scalar(
        select(GatewayPolicyRow.id)
        .where(
            GatewayPolicyRow.project_id == project,
            GatewayPolicyRow.id != row.id,
            GatewayPolicyRow.approved_at.is_not(None),
            GatewayPolicyRow.starts_at < row.expires_at,
            GatewayPolicyRow.expires_at > row.starts_at,
        )
        .limit(1)
    )
    if overlap:
        raise GatewayError("GATEWAY_APPROVED_WINDOWS_MUST_NOT_OVERLAP", 409)
    for model in specification.models:
        qualify_model(model)
    row.state, row.approved_by, row.approval_ref, row.approved_at = (
        "APPROVED",
        actor,
        approval_ref,
        now,
    )
    limits = [("project", str(project), specification.project_limit)]
    limits += [("team", str(team.team_id), team.limit) for team in specification.teams]
    limits += [("model", model.key, model.limit) for model in specification.models]
    for kind, key, _ in limits:
        session.add(
            GatewayBudgetRow(project_id=project, policy_id=row.id, scope_kind=kind, scope_key=key)
        )
    session.flush()
    audit(session, project, actor, "gateway_policy_approved", policy_id=str(row.id))
    return row


def resume_policy(session, project, row_id, approval_hash, approval_ref, actor, *, now=None):
    """Resume the original window without resetting money, request counts or operation IDs."""
    lock_project(session, project)
    now = now or datetime.now(UTC)
    current_admin(session, project, actor)
    row = session.get(GatewayPolicyRow, row_id)
    if row is None or row.project_id != project:
        raise GatewayError("GATEWAY_POLICY_NOT_FOUND", 404)
    specification = policy_spec(row)
    if not hmac.compare_digest(row.approval_hash, approval_hash):
        raise GatewayError("GATEWAY_EXACT_APPROVAL_HASH_REQUIRED", 409)
    if (
        row.state == "APPROVED"
        and row.approved_by == actor
        and row.approval_ref == approval_ref
        and row.expires_at > now
    ):
        return row
    if (
        row.state not in {"DISABLED", "BLOCKED"}
        or row.approved_at is None
        or not row.starts_at <= now < row.expires_at
    ):
        raise GatewayError("GATEWAY_POLICY_NOT_RESUMABLE", 409)
    for model in specification.models:
        qualify_model(model)
    prior_ref = row.approval_ref
    row.state, row.approved_by, row.approval_ref = "APPROVED", actor, approval_ref
    audit(
        session,
        project,
        actor,
        "gateway_policy_resumed_with_retained_limits",
        policy_id=str(row.id),
        approval_ref=str(approval_ref),
        prior_approval_ref=str(prior_ref),
    )
    return row


def issue_key(session, project, specification: GatewayKeyInput, actor, *, now=None):
    lock_project(session, project)
    now = now or datetime.now(UTC)
    current_admin(session, project, actor)
    prior = session.scalar(
        select(GatewayKeyRow.id).where(
            GatewayKeyRow.project_id == project,
            GatewayKeyRow.request_id == specification.request_id,
        )
    )
    if prior:
        # A lost credential response is not permission to mint another secret.
        # The one-way digest cannot recover the original plaintext token.
        raise GatewayError("GATEWAY_KEY_ALREADY_ISSUED_REVOKE_BEFORE_REPLACEMENT", 409)
    policy = session.get(GatewayPolicyRow, specification.policy_id)
    if policy is None or policy.project_id != project:
        raise GatewayError("GATEWAY_POLICY_NOT_FOUND", 404)
    envelope = policy_spec(policy)
    if policy.state != "APPROVED" or now >= policy.expires_at:
        raise GatewayError("GATEWAY_APPROVED_POLICY_REQUIRED", 409)
    current_admin(session, project, policy.approved_by)
    team = next((team for team in envelope.teams if team.team_id == specification.team_id), None)
    models = {model.key: model for model in envelope.models}
    if (
        team is None
        or specification.environment_id not in envelope.environment_ids
        or not set(specification.model_keys).issubset(models)
        or not now < specification.expires_at <= policy.expires_at
    ):
        raise GatewayError("GATEWAY_KEY_OUTSIDE_APPROVED_POLICY", 403)
    parents = [team.limit, envelope.project_limit] + [
        models[k].limit for k in specification.model_keys
    ]
    if any(
        specification.limit.modeled_usd > limit.modeled_usd
        or specification.limit.requests > limit.requests
        or specification.limit.concurrent_requests > limit.concurrent_requests
        for limit in parents
    ):
        raise GatewayError("GATEWAY_KEY_LIMIT_EXCEEDS_POLICY", 422)
    if any(
        upper_cost(models[k]) > specification.limit.modeled_usd for k in specification.model_keys
    ):
        raise GatewayError("GATEWAY_KEY_LIMIT_BELOW_ONE_FULL_RESERVATION", 422)
    if (
        session.scalar(
            select(func.count())
            .select_from(GatewayKeyRow)
            .where(GatewayKeyRow.project_id == project)
        )
        >= 1000
    ):
        raise GatewayError("GATEWAY_KEY_CAPACITY_REACHED", 429)
    token = "cbg_" + new_token()
    row = GatewayKeyRow(
        id=uuid4(),
        project_id=project,
        policy_id=policy.id,
        request_id=specification.request_id,
        environment_id=specification.environment_id,
        team_id=specification.team_id,
        specification=specification.model_dump(mode="json"),
        issued_by=actor,
        digest=token_hash(token),
        expires_at=specification.expires_at,
    )
    session.add(row)
    session.add(
        GatewayBudgetRow(
            project_id=project, policy_id=policy.id, scope_kind="key", scope_key=str(row.id)
        )
    )
    session.flush()
    audit(
        session, project, actor, "gateway_key_issued", key_id=str(row.id), policy_id=str(policy.id)
    )
    return row, token


def active_key(session, token, now):
    if type(token) is not str or not token.startswith("cbg_") or not 50 <= len(token) <= 100:
        raise GatewayError("GATEWAY_VIRTUAL_KEY_REQUIRED", 401)
    row = session.scalar(select(GatewayKeyRow).where(GatewayKeyRow.digest == token_hash(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise GatewayError("GATEWAY_INVALID_OR_EXPIRED_KEY", 401)
    return row


def budget_scopes(policy, key, model):
    envelope = policy_spec(policy)
    key_spec = GatewayKeyInput.model_validate(key.specification)
    if (
        key_spec.policy_id != policy.id
        or key_spec.environment_id != key.environment_id
        or key_spec.team_id != key.team_id
        or key_spec.expires_at != key.expires_at
        or key_spec.request_id != key.request_id
        or model.key not in key_spec.model_keys
    ):
        raise GatewayError("GATEWAY_KEY_SCOPE_INTEGRITY_FAILURE", 409)
    team = next((team for team in envelope.teams if team.team_id == key.team_id), None)
    if team is None or key.environment_id not in envelope.environment_ids:
        raise GatewayError("GATEWAY_KEY_SCOPE_INTEGRITY_FAILURE", 409)
    return [
        ("project", str(key.project_id), envelope.project_limit),
        ("team", str(key.team_id), team.limit),
        ("model", model.key, model.limit),
        ("key", str(key.id), key_spec.limit),
    ]


@dataclass(frozen=True)
class Reservation:
    project_id: UUID
    environment_id: UUID
    policy_id: UUID
    key_id: UUID
    operation_id: UUID
    event_id: UUID
    environment: str
    model: ModelLimit | None


@exact_accounting
def reserve(
    sessions,
    token,
    operation_id,
    request_hmac,
    request: ResponsesText,
    *,
    now=None,
    traceparent=None,
):
    if operation_id.int == 0 or len(request_hmac) != 64:
        raise GatewayError("GATEWAY_OPERATION_ID_REQUIRED", 400)
    with sessions() as session:
        key = active_key(session, token, now or datetime.now(UTC))
        project = key.project_id
    with sessions.begin() as session:
        lock_project(session, project)
        # A queued mutation may outlive its key or approved window. Refresh the
        # real clock after acquiring the shared lock, before reserving money.
        now = now or datetime.now(UTC)
        key = active_key(session, token, now)
        policy = session.get(GatewayPolicyRow, key.policy_id)
        if (
            policy is None
            or policy.state != "APPROVED"
            or not policy.starts_at <= now < policy.expires_at
        ):
            raise GatewayError("GATEWAY_ACTIVE_POLICY_REQUIRED", 403)
        current_admin(session, project, policy.approved_by)
        current_admin(session, project, key.issued_by)
        envelope = policy_spec(policy)
        model = next((m for m in envelope.models if m.model == request.model), None)
        if model is None:
            raise GatewayError("GATEWAY_MODEL_NOT_APPROVED", 403)
        qualify_request(request, model)
        prior = session.get(GatewayCallRow, (project, operation_id))
        if prior is not None:
            same = hmac.compare_digest(prior.request_hmac, request_hmac) and prior.key_id == key.id
            raise GatewayError(
                "GATEWAY_OPERATION_ALREADY_DISPATCHED_NO_REPLAY"
                if same
                else "GATEWAY_OPERATION_ID_CONFLICT",
                409,
            )
        if (
            session.scalar(
                select(func.count())
                .select_from(GatewayCallRow)
                .where(GatewayCallRow.project_id == project)
            )
            >= 1000000
        ):
            raise GatewayError("GATEWAY_RECEIPT_CAPACITY_REACHED", 429)
        amount = upper_cost(model)
        rows = []
        for kind, scope_key, limit in budget_scopes(policy, key, model):
            row = session.get(GatewayBudgetRow, (policy.id, kind, scope_key))
            if row is None or row.project_id != project:
                raise GatewayError("GATEWAY_BUDGET_INTEGRITY_FAILURE", 409)
            if row.spent_usd + row.reserved_usd + amount > limit.modeled_usd:
                raise GatewayError("GATEWAY_MODELED_BUDGET_EXHAUSTED", 429)
            if row.requests_used >= limit.requests:
                raise GatewayError("GATEWAY_REQUEST_BUDGET_EXHAUSTED", 429)
            if row.active_requests >= limit.concurrent_requests:
                raise GatewayError("GATEWAY_CONCURRENCY_EXHAUSTED", 429)
            rows.append(row)
        for row in rows:
            row.requests_used += 1
            row.active_requests += 1
            row.reserved_usd += amount
        call = GatewayCallRow(
            project_id=project,
            operation_id=operation_id,
            environment_id=key.environment_id,
            policy_id=policy.id,
            key_id=key.id,
            team_id=key.team_id,
            model_key=model.key,
            request_hmac=request_hmac,
            max_output_tokens=request.max_output_tokens,
            traffic_kind=GatewayKeyInput.model_validate(key.specification).traffic_kind,
            price_sha256=model.price_contract.price_sha256,
            reserved_usd=amount,
            lease_until=now + timedelta(seconds=model.timeout_seconds + 30),
        )
        session.add(call)
        session.flush()
        environment = session.get(Environment, key.environment_id)
        if environment is None or environment.project_id != project:
            raise GatewayError("GATEWAY_ENVIRONMENT_SCOPE_DENIED", 403)
        from cutback.server.gateway_metering import persist_event, placeholder

        persist_event(
            session, call, placeholder(call, environment.name, traceparent=traceparent), None
        )
        return Reservation(
            project,
            key.environment_id,
            policy.id,
            key.id,
            operation_id,
            call.event_id,
            environment.name,
            model,
        )


def accounting_rows(session, call):
    identities = [
        ("project", str(call.project_id)),
        ("team", str(call.team_id)),
        ("model", call.model_key),
        ("key", str(call.key_id)),
    ]
    rows = [session.get(GatewayBudgetRow, (call.policy_id, kind, key)) for kind, key in identities]
    found = []
    for row in rows:
        if row is None or row.project_id != call.project_id:
            raise GatewayError("GATEWAY_BUDGET_INTEGRITY_FAILURE", 409)
        found.append(row)
    return found


@exact_accounting
def finish(
    sessions,
    reservation,
    amount: Decimal | None,
    *,
    code=None,
    event=None,
    now=None,
    only_expired=False,
):
    """Late known receipts may settle uncertainty; a failed acknowledgement never replays HTTP."""
    now = now or datetime.now(UTC)
    with sessions.begin() as session:
        lock_project(session, reservation.project_id)
        call = session.get(GatewayCallRow, (reservation.project_id, reservation.operation_id))
        if (
            call is None
            or call.key_id != reservation.key_id
            or call.policy_id != reservation.policy_id
        ):
            raise GatewayError("GATEWAY_RESERVATION_NOT_FOUND", 409)
        if only_expired and (call.state != "DISPATCHED" or call.lease_until > now):
            return False
        if code in {
            "GATEWAY_ACTUAL_RESPONSE_OUTSIDE_APPROVED_CONTRACT",
            "GATEWAY_MALFORMED_USAGE_BILLING_UNCERTAIN",
            "GATEWAY_UPSTREAM_ENCODING_UNQUALIFIED",
            "GATEWAY_UPSTREAM_SURFACE_UNQUALIFIED",
            "GATEWAY_RESPONSE_TOO_LARGE_BILLING_UNCERTAIN",
        }:
            policy = session.get(GatewayPolicyRow, call.policy_id)
            if policy is not None:
                policy.state = "BLOCKED"
        if amount is not None and (
            type(amount) is not Decimal
            or not amount.is_finite()
            or amount < 0
            or amount > call.reserved_usd
            or amount != amount.quantize(Decimal("0.000000000001"))
        ):
            amount, code = None, "GATEWAY_COST_OUTSIDE_APPROVED_BOUNDS"
            policy = session.get(GatewayPolicyRow, call.policy_id)
            if policy is not None:
                policy.state = "BLOCKED"
        if call.state == "SETTLED":
            if call.cost_usd != amount:
                raise GatewayError("GATEWAY_SETTLEMENT_CONFLICT", 409)
            return
        rows = accounting_rows(session, call)
        for row in rows:
            if call.state == "DISPATCHED":
                row.active_requests -= 1
            if amount is None and call.state == "DISPATCHED":
                row.uncertain_requests += 1
            elif amount is not None:
                row.reserved_usd -= call.reserved_usd
                row.spent_usd += amount
                if call.state == "UNCERTAIN":
                    row.uncertain_requests -= 1
        call.state = "UNCERTAIN" if amount is None else "SETTLED"
        call.cost_usd, call.code, call.completed_at = amount, code, now
        if event is not None:
            from cutback.server.gateway_metering import persist_event

            persist_event(session, call, event, amount, allow_insert=False)
        return True


def recover_leases(sessions, *, project_id=None, now=None):
    """Bounded recovery changes accounting state, never retries a provider call."""
    now = now or datetime.now(UTC)
    with sessions() as session:
        query = (
            select(GatewayCallRow)
            .where(GatewayCallRow.state == "DISPATCHED", GatewayCallRow.lease_until <= now)
            .order_by(GatewayCallRow.lease_until, GatewayCallRow.project_id)
            .limit(100)
        )
        if project_id is not None:
            query = query.where(GatewayCallRow.project_id == project_id)
        calls = session.scalars(query).all()
    recovered = 0
    for call in calls:
        reservation = Reservation(
            call.project_id,
            call.environment_id,
            call.policy_id,
            call.key_id,
            call.operation_id,
            call.event_id,
            "",
            None,
        )
        recovered += bool(
            finish(
                sessions,
                reservation,
                None,
                code="GATEWAY_LEASE_EXPIRED_BILLING_UNCERTAIN",
                now=now,
                only_expired=True,
            )
        )
    return recovered


def maintain_gateway(session, selected, cutoff, aggregate_cutoff):
    """Retain counters beyond raw calls; never reset active limits or unknown dollars."""
    expired_raw = select(GatewayPolicyRow.id).where(
        GatewayPolicyRow.expires_at < cutoff, selected(GatewayPolicyRow.project_id)
    )
    session.execute(
        delete(GatewayCallRow).where(
            GatewayCallRow.policy_id.in_(expired_raw),
            GatewayCallRow.state != "DISPATCHED",
            GatewayCallRow.completed_at < cutoff,
            selected(GatewayCallRow.project_id),
        )
    )
    live = exists(
        select(GatewayCallRow.operation_id).where(
            GatewayCallRow.policy_id == GatewayPolicyRow.id,
            GatewayCallRow.state == "DISPATCHED",
        )
    )
    session.execute(
        delete(GatewayPolicyRow).where(
            GatewayPolicyRow.expires_at < aggregate_cutoff,
            ~live,
            selected(GatewayPolicyRow.project_id),
        )
    )
