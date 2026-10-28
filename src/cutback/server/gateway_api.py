"""Scoped gateway controls. Provider credentials belong only to the separate gateway."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from cutback.core.models import StrictModel
from cutback.gateway.contracts import GatewayKeyInput, GatewayPolicy, SpendLimit, upper_cost
from cutback.server.gateway_db import (
    GatewayBudgetRow,
    GatewayCallRow,
    GatewayKeyRow,
    GatewayPolicyRow,
)
from cutback.server.gateway_service import (
    GatewayError,
    approve_policy,
    audit,
    create_policy,
    current_admin,
    issue_key,
    lock_project,
    policy_spec,
    require_state_version,
    resume_policy,
)


class GatewayApproval(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_ref: UUID
    expected_state_version: int = Field(strict=True, ge=0, le=9007199254740991)


class GatewayExactPolicy(StrictModel):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_state_version: int = Field(strict=True, ge=0, le=9007199254740991)


class GatewayPolicyReceipt(StrictModel):
    id: UUID
    specification: GatewayPolicy
    approval_hash: str
    state: str
    state_version: int
    approved_at: datetime | None
    created_at: datetime
    qualified_reservations_usd: dict[str, Decimal | None]
    budget_evidence: str = "frozen_public_list_price_model_not_invoice_limit"
    provider_credentials_configured: None = None


class GatewayBudgetReceipt(StrictModel):
    scope_kind: Literal["project", "team", "model", "key"]
    scope_key: str
    limit: SpendLimit
    requests_used: int
    active_requests: int
    uncertain_requests: int
    known_modeled_spent_usd: Decimal
    retained_reservations_usd: Decimal
    remaining_modeled_usd: Decimal
    complete_modeled_cost_usd: Decimal | None


class GatewayKeyReceipt(StrictModel):
    id: UUID
    policy_id: UUID
    environment_id: UUID
    team_id: UUID
    specification: GatewayKeyInput
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime


class GatewayIssuedKey(StrictModel):
    key: GatewayKeyReceipt
    token: str
    display_once: Literal[True] = True


class GatewayCallReceipt(StrictModel):
    operation_id: UUID
    environment_id: UUID
    policy_id: UUID
    key_id: UUID
    team_id: UUID
    model_key: str
    traffic_kind: str
    max_output_tokens: int
    state: str
    price_sha256: str
    reserved_usd: Decimal
    cost_usd: Decimal | None
    code: str | None
    created_at: datetime
    completed_at: datetime | None


class GatewayPolicyPage(StrictModel):
    items: list[GatewayPolicyReceipt]
    next_after: UUID | None


class GatewayKeyPage(StrictModel):
    items: list[GatewayKeyReceipt]
    next_after: UUID | None


class GatewayCallPage(StrictModel):
    items: list[GatewayCallReceipt]
    next_after: UUID | None


class GatewayPolicyDetail(StrictModel):
    policy: GatewayPolicyReceipt
    budgets: list[GatewayBudgetReceipt]


def policy_receipt(row):
    state = "EXPIRED" if row.expires_at <= datetime.now(UTC) else row.state
    specification = policy_spec(row)
    bounds = {}
    for model in specification.models:
        try:
            bounds[model.key] = upper_cost(model)
        except ValueError:
            bounds[model.key] = None
    return GatewayPolicyReceipt(
        id=row.id,
        specification=specification,
        qualified_reservations_usd=bounds,
        approval_hash=row.approval_hash,
        state=state,
        state_version=row.state_version,
        approved_at=row.approved_at,
        created_at=row.created_at,
    )


def key_receipt(row):
    return GatewayKeyReceipt(
        id=row.id,
        policy_id=row.policy_id,
        environment_id=row.environment_id,
        team_id=row.team_id,
        specification=GatewayKeyInput.model_validate(row.specification),
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        created_at=row.created_at,
    )


def install_gateway_controls(app, sessions, authorize, scope):
    @app.exception_handler(GatewayError)
    async def gateway_error(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.code,
                "request_id": request.state.request_id,
                "retryable": False,
            },
        )

    def unrestricted(auth):
        if auth.get("environment_id") is not None:
            raise HTTPException(403, "GATEWAY_PROJECT_WIDE_ADMIN_REQUIRED")

    def visible(row, project, auth):
        if row is None or row.project_id != project:
            raise HTTPException(404, "GATEWAY_POLICY_NOT_FOUND")
        environment = auth.get("environment_id")
        if environment is not None and policy_spec(row).environment_ids != [environment]:
            raise HTTPException(403, "GATEWAY_ENVIRONMENT_SCOPE_DENIED")
        return row

    @app.post("/v1/projects/{project_id}/gateway/policies", status_code=201)
    def create(
        project_id: UUID, body: GatewayPolicy, auth=Depends(authorize)
    ) -> GatewayPolicyReceipt:
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        try:
            with sessions.begin() as session:
                return policy_receipt(create_policy(session, project_id, body, auth["actor"]))
        except ValueError:
            raise HTTPException(422, "GATEWAY_POLICY_CONTRACT_UNQUALIFIED") from None

    @app.get("/v1/projects/{project_id}/gateway/policies")
    def policies(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        auth=Depends(authorize),
    ) -> GatewayPolicyPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(GatewayPolicyRow).where(GatewayPolicyRow.project_id == project_id)
            if auth.get("environment_id") is not None:
                query = query.where(
                    GatewayPolicyRow.specification["environment_ids"].as_string()
                    == '["' + str(auth["environment_id"]) + '"]'
                )
            if after:
                query = query.where(GatewayPolicyRow.id > after)
            rows = session.scalars(query.order_by(GatewayPolicyRow.id).limit(limit + 1)).all()
            return GatewayPolicyPage(
                items=[policy_receipt(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/gateway/policies/{policy_id}")
    def detail(project_id: UUID, policy_id: UUID, auth=Depends(authorize)) -> GatewayPolicyDetail:
        scope(auth, project_id)
        with sessions() as session:
            row = visible(session.get(GatewayPolicyRow, policy_id), project_id, auth)
            envelope = policy_spec(row)
            limits = {("project", str(project_id)): envelope.project_limit}
            limits.update({("team", str(t.team_id)): t.limit for t in envelope.teams})
            limits.update({("model", m.key): m.limit for m in envelope.models})
            for key in session.scalars(
                select(GatewayKeyRow).where(
                    GatewayKeyRow.project_id == project_id, GatewayKeyRow.policy_id == policy_id
                )
            ):
                limits[("key", str(key.id))] = GatewayKeyInput.model_validate(
                    key.specification
                ).limit
            rows = session.scalars(
                select(GatewayBudgetRow)
                .where(
                    GatewayBudgetRow.project_id == project_id,
                    GatewayBudgetRow.policy_id == policy_id,
                )
                .order_by(GatewayBudgetRow.scope_kind, GatewayBudgetRow.scope_key)
            ).all()
            budgets = []
            for budget in rows:
                maximum = limits[(budget.scope_kind, budget.scope_key)]
                with localcontext() as money_context:
                    money_context.prec = 80
                    remaining = max(
                        Decimal(0), maximum.modeled_usd - budget.spent_usd - budget.reserved_usd
                    )
                budgets.append(
                    GatewayBudgetReceipt(
                        scope_kind=budget.scope_kind,
                        scope_key=budget.scope_key,
                        limit=maximum,
                        requests_used=budget.requests_used,
                        active_requests=budget.active_requests,
                        uncertain_requests=budget.uncertain_requests,
                        known_modeled_spent_usd=budget.spent_usd,
                        retained_reservations_usd=budget.reserved_usd,
                        remaining_modeled_usd=remaining,
                        complete_modeled_cost_usd=(
                            budget.spent_usd
                            if not (budget.active_requests or budget.uncertain_requests)
                            else None
                        ),
                    )
                )
            return GatewayPolicyDetail(policy=policy_receipt(row), budgets=budgets)

    @app.post("/v1/projects/{project_id}/gateway/policies/{policy_id}/approve")
    def approve(
        project_id: UUID, policy_id: UUID, body: GatewayApproval, auth=Depends(authorize)
    ) -> GatewayPolicyReceipt:
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        if body.approval_ref.int == 0:
            raise HTTPException(422, "GATEWAY_NONZERO_APPROVAL_REFERENCE_REQUIRED")
        try:
            with sessions.begin() as session:
                return policy_receipt(
                    approve_policy(
                        session,
                        project_id,
                        policy_id,
                        body.approval_hash,
                        body.approval_ref,
                        auth["actor"],
                        expected_state_version=body.expected_state_version,
                    )
                )
        except ValueError:
            raise HTTPException(422, "GATEWAY_POLICY_CONTRACT_UNQUALIFIED") from None

    @app.post("/v1/projects/{project_id}/gateway/policies/{policy_id}/disable")
    def disable(
        project_id: UUID, policy_id: UUID, body: GatewayExactPolicy, auth=Depends(authorize)
    ) -> GatewayPolicyReceipt:
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        with sessions.begin() as session:
            lock_project(session, project_id)
            current_admin(session, project_id, auth["actor"])
            row = visible(session.get(GatewayPolicyRow, policy_id), project_id, auth)
            if body.approval_hash != row.approval_hash:
                raise HTTPException(409, "GATEWAY_EXACT_APPROVAL_HASH_REQUIRED")
            if row.state == "DISABLED" and row.state_version == body.expected_state_version + 1:
                return policy_receipt(row)
            require_state_version(row, body.expected_state_version)
            # A new explicit stop while already disabled invalidates prepared
            # activations too. A duplicate of that same stop remains idempotent.
            row.state = "DISABLED"
            row.state_version += 1
            audit(
                session,
                project_id,
                auth["actor"],
                "gateway_policy_disabled",
                policy_id=str(policy_id),
                state_version=row.state_version,
            )
            return policy_receipt(row)

    @app.post("/v1/projects/{project_id}/gateway/policies/{policy_id}/resume")
    def resume(
        project_id: UUID, policy_id: UUID, body: GatewayApproval, auth=Depends(authorize)
    ) -> GatewayPolicyReceipt:
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        if body.approval_ref.int == 0:
            raise HTTPException(422, "GATEWAY_NONZERO_APPROVAL_REFERENCE_REQUIRED")
        try:
            with sessions.begin() as session:
                return policy_receipt(
                    resume_policy(
                        session,
                        project_id,
                        policy_id,
                        body.approval_hash,
                        body.approval_ref,
                        auth["actor"],
                        expected_state_version=body.expected_state_version,
                    )
                )
        except ValueError:
            raise HTTPException(422, "GATEWAY_POLICY_CONTRACT_UNQUALIFIED") from None

    @app.post("/v1/projects/{project_id}/gateway/keys", status_code=201)
    def issue(project_id: UUID, body: GatewayKeyInput, auth=Depends(authorize)) -> GatewayIssuedKey:
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        try:
            with sessions.begin() as session:
                row, token = issue_key(session, project_id, body, auth["actor"])
                return GatewayIssuedKey(key=key_receipt(row), token=token)
        except ValueError:
            raise HTTPException(422, "GATEWAY_KEY_CONTRACT_UNQUALIFIED") from None

    @app.get("/v1/projects/{project_id}/gateway/keys")
    def keys(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        auth=Depends(authorize),
    ) -> GatewayKeyPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(GatewayKeyRow).where(GatewayKeyRow.project_id == project_id)
            if auth.get("environment_id") is not None:
                query = query.where(GatewayKeyRow.environment_id == auth["environment_id"])
            if after:
                query = query.where(GatewayKeyRow.id > after)
            rows = session.scalars(query.order_by(GatewayKeyRow.id).limit(limit + 1)).all()
            return GatewayKeyPage(
                items=[key_receipt(row) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.delete("/v1/projects/{project_id}/gateway/keys/{key_id}")
    def revoke(project_id: UUID, key_id: UUID, auth=Depends(authorize)):
        scope(auth, project_id, ("admin",))
        unrestricted(auth)
        with sessions.begin() as session:
            lock_project(session, project_id)
            current_admin(session, project_id, auth["actor"])
            row = session.get(GatewayKeyRow, key_id)
            if row is None or row.project_id != project_id:
                raise HTTPException(404, "GATEWAY_KEY_NOT_FOUND")
            if row.revoked_at is None:
                row.revoked_at = datetime.now(UTC)
                audit(session, project_id, auth["actor"], "gateway_key_revoked", key_id=str(key_id))
        return {"status": "revoked"}

    @app.get("/v1/projects/{project_id}/gateway/calls")
    def calls(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        auth=Depends(authorize),
    ) -> GatewayCallPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(GatewayCallRow).where(GatewayCallRow.project_id == project_id)
            if auth.get("environment_id") is not None:
                query = query.where(GatewayCallRow.environment_id == auth["environment_id"])
            if after:
                query = query.where(GatewayCallRow.operation_id > after)
            rows = session.scalars(
                query.order_by(GatewayCallRow.operation_id).limit(limit + 1)
            ).all()
            return GatewayCallPage(
                items=[
                    GatewayCallReceipt.model_validate(row, from_attributes=True)
                    for row in rows[:limit]
                ],
                next_after=rows[limit - 1].operation_id if len(rows) > limit else None,
            )
