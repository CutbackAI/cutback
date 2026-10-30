"""Finite cleanup-only recovery. Never restarts a tool or repeats an operation."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select

from cutback.core.tool_policy import ToolContainmentScope
from cutback.server.tool_db import ToolSessionRecord
from cutback.server.tool_execution import configured_tool_client


def scope_for(row):
    return ToolContainmentScope(
        session_id=row.id,
        project_id=row.project_id,
        principal_id=row.principal_id,
        broker_id=row.broker_id,
        environment=row.environment_name,
        workflow=row.workflow,
        image_id=row.image_id,
        policy_sha256=row.policy_sha256,
    )


def recover_tool_one(sessions, *, client=None, now=None):
    now = now or datetime.now(UTC)
    try:
        project = client.project_id if client else UUID(os.environ["CUTBACK_TOOL_BROKER_PROJECT"])
        principal = (
            client.principal_id if client else UUID(os.environ["CUTBACK_TOOL_BROKER_PRINCIPAL"])
        )
        broker = client.broker_id if client else UUID(os.environ["CUTBACK_TOOL_BROKER_ID"])
    except (ValueError, KeyError):
        return {"status": "NOT_CONFIGURED"}
    with sessions.begin() as session:
        row = session.scalar(
            select(ToolSessionRecord)
            .where(
                ToolSessionRecord.project_id == project,
                ToolSessionRecord.principal_id == principal,
                ToolSessionRecord.broker_id == broker,
                ToolSessionRecord.cleanup_confirmed.is_(False),
                ToolSessionRecord.cleanup_attempts < 3,
                or_(
                    ToolSessionRecord.cleanup_requested.is_(True),
                    ToolSessionRecord.lease_until <= now,
                ),
                or_(
                    ToolSessionRecord.cleanup_lease_until.is_(None),
                    ToolSessionRecord.cleanup_lease_until <= now,
                ),
            )
            .order_by(ToolSessionRecord.created_at, ToolSessionRecord.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not row:
            return {"status": "IDLE"}
        scope = scope_for(row)
        row.cleanup_attempts += 1
        attempt = row.cleanup_attempts
        row.cleanup_lease_until = now + timedelta(seconds=180)
        row.state = "UNCERTAIN"
    owned = client is None
    try:
        client = client or configured_tool_client(scope, containment=True)
        receipt = client.contain(scope)
        confirmed = (
            receipt.get("cleanup_confirmed") is True
            and receipt.get("state") == "CLOSED"
            and all(
                receipt.get(key) == value for key, value in scope.model_dump(mode="json").items()
            )
        )
    except Exception:
        confirmed = False
    finally:
        if owned and client is not None:
            client.close()
    with sessions.begin() as session:
        row = session.get(ToolSessionRecord, scope.session_id, with_for_update=True)
        if not row or row.project_id != scope.project_id or row.cleanup_attempts != attempt:
            return {"status": "STALE_CONTAINMENT_RECEIPT"}
        if confirmed:
            row.cleanup_confirmed, row.cleanup_requested, row.state = True, False, "CLOSED"
            row.completed_at = datetime.now(UTC)
        # Even cleanup retries are finite; uncertainty remains after three attempts.
        return {
            "status": "CLOSED" if confirmed else "UNCERTAIN",
            "session_id": str(row.id),
            "cleanup_attempts": row.cleanup_attempts,
            "tool_operations_replayed": 0,
        }
