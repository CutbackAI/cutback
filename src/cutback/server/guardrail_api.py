"""Runtime policy registration and audit. Ordinary reads never disclose literals."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Depends, Query

from cutback.core.guardrails import (
    GuardrailBinding,
    GuardrailDecision,
    GuardrailPolicy,
    GuardrailStage,
)
from cutback.core.models import StrictModel
from cutback.server.campaign_content import content_transaction
from cutback.server.evaluation_api import ContentPurpose, require_store
from cutback.server.guardrail_controls import (
    GuardrailActivation,
    GuardrailDisable,
    GuardrailRegistration,
    activate,
    disable,
    load_private_policy,
    metadata,
    register,
    scoped,
)
from cutback.server.guardrail_db import GuardrailOperationRow, GuardrailPolicyRow


class GuardrailControlReceipt(StrictModel):
    id: UUID
    binding: GuardrailBinding
    capture_policy_id: UUID
    approval_hash: str
    state: str
    state_version: int
    max_checks: int
    checks_used: int
    approved_at: datetime | None
    created_at: datetime
    execution_authority: Literal["current", "ended", "inactive", "unknown"]


class GuardrailPolicyPage(StrictModel):
    items: list[GuardrailControlReceipt]
    next_after: UUID | None


class GuardrailOperationReceipt(StrictModel):
    policy_id: UUID
    operation_id: UUID
    stage: GuardrailStage
    state: Literal["RESERVED", "COMPLETED", "UNKNOWN"]
    decision: GuardrailDecision | None
    created_at: datetime
    completed_at: datetime | None


class GuardrailOperationPage(StrictModel):
    items: list[GuardrailOperationReceipt]
    next_after: str | None


def operation_metadata(row):
    return GuardrailOperationReceipt(
        policy_id=row.policy_id,
        operation_id=row.operation_id,
        stage=row.stage,
        state=row.state,
        decision=row.decision,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )


def install_guardrail_controls(app, sessions, authorize, scope):
    from sqlalchemy import select

    @app.post("/v1/projects/{project_id}/runtime-guardrails", status_code=201)
    def create(
        project_id: UUID, body: GuardrailRegistration, auth=Depends(authorize)
    ) -> GuardrailControlReceipt:
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with content_transaction(sessions, store, project_id) as (session, refs):
            row = register(session, store, project_id, body, auth["actor"], refs)
            return GuardrailControlReceipt.model_validate(metadata(row, session))

    @app.get("/v1/projects/{project_id}/runtime-guardrails")
    def listing(
        project_id: UUID,
        after: UUID | None = None,
        limit: int = Query(default=25, ge=1, le=100),
        auth=Depends(authorize),
    ) -> GuardrailPolicyPage:
        scope(auth, project_id)
        with sessions() as session:
            query = select(GuardrailPolicyRow).where(GuardrailPolicyRow.project_id == project_id)
            if after:
                query = query.where(GuardrailPolicyRow.id > after)
            rows = session.scalars(query.order_by(GuardrailPolicyRow.id).limit(limit + 1)).all()
            return GuardrailPolicyPage(
                items=[metadata(row, session) for row in rows[:limit]],
                next_after=rows[limit - 1].id if len(rows) > limit else None,
            )

    @app.get("/v1/projects/{project_id}/runtime-guardrails/{policy_id}")
    def detail(
        project_id: UUID, policy_id: UUID, auth=Depends(authorize)
    ) -> GuardrailControlReceipt:
        scope(auth, project_id)
        with sessions() as session:
            return GuardrailControlReceipt.model_validate(
                metadata(scoped(session, project_id, policy_id), session)
            )

    @app.post("/v1/projects/{project_id}/runtime-guardrails/{policy_id}/approve")
    def approve(
        project_id: UUID, policy_id: UUID, body: GuardrailActivation, auth=Depends(authorize)
    ) -> GuardrailControlReceipt:
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            return GuardrailControlReceipt.model_validate(
                metadata(activate(session, project_id, policy_id, body, auth["actor"]), session)
            )

    @app.post("/v1/projects/{project_id}/runtime-guardrails/{policy_id}/disable")
    def turn_off(
        project_id: UUID, policy_id: UUID, body: GuardrailDisable, auth=Depends(authorize)
    ) -> GuardrailControlReceipt:
        scope(auth, project_id, ("admin",))
        with sessions.begin() as session:
            return GuardrailControlReceipt.model_validate(
                metadata(disable(session, project_id, policy_id, body, auth["actor"]), session)
            )

    @app.post("/v1/projects/{project_id}/runtime-guardrails/{policy_id}/content")
    def content(
        project_id: UUID, policy_id: UUID, body: ContentPurpose, auth=Depends(authorize)
    ) -> GuardrailPolicy:
        scope(auth, project_id, ("admin",))
        store = require_store(app)
        with sessions.begin() as session:
            return load_private_policy(
                session, store, scoped(session, project_id, policy_id), auth["actor"], body.purpose
            )

    @app.get("/v1/projects/{project_id}/runtime-guardrails/{policy_id}/operations")
    def operations(
        project_id: UUID,
        policy_id: UUID,
        after: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=25, ge=1, le=100),
        auth=Depends(authorize),
    ) -> GuardrailOperationPage:
        from fastapi import HTTPException
        from sqlalchemy import and_, or_

        scope(auth, project_id)
        with sessions() as session:
            scoped(session, project_id, policy_id)
            query = select(GuardrailOperationRow).where(
                GuardrailOperationRow.project_id == project_id,
                GuardrailOperationRow.policy_id == policy_id,
            )
            if after:
                try:
                    operation_text, stage = after.split(":")
                    operation = UUID(operation_text)
                    if stage not in {"input", "output", "tool_input", "tool_output"}:
                        raise ValueError("STAGE")
                except (ValueError, TypeError):
                    raise HTTPException(422, "GUARDRAIL_CURSOR_INVALID") from None
                query = query.where(
                    or_(
                        GuardrailOperationRow.operation_id > operation,
                        and_(
                            GuardrailOperationRow.operation_id == operation,
                            GuardrailOperationRow.stage > stage,
                        ),
                    )
                )
            rows = session.scalars(
                query.order_by(
                    GuardrailOperationRow.operation_id, GuardrailOperationRow.stage
                ).limit(limit + 1)
            ).all()
            cursor = (
                f"{rows[limit - 1].operation_id}:{rows[limit - 1].stage}"
                if len(rows) > limit
                else None
            )
            return GuardrailOperationPage(
                items=[operation_metadata(row) for row in rows[:limit]], next_after=cursor
            )

    @app.get(
        "/v1/projects/{project_id}/runtime-guardrails/{policy_id}/operations/{operation_id}/{stage}"
    )
    def operation(
        project_id: UUID, policy_id: UUID, operation_id: UUID, stage: str, auth=Depends(authorize)
    ) -> GuardrailOperationReceipt:
        scope(auth, project_id)
        with sessions() as session:
            scoped(session, project_id, policy_id)
            row = session.get(GuardrailOperationRow, (project_id, policy_id, operation_id, stage))
            if row is None:
                from fastapi import HTTPException

                raise HTTPException(404, "GUARDRAIL_CHECK_NOT_FOUND")
            return GuardrailOperationReceipt(
                policy_id=row.policy_id,
                operation_id=row.operation_id,
                stage=row.stage,
                state=row.state,
                decision=row.decision,
                created_at=row.created_at,
                completed_at=row.completed_at,
            )
