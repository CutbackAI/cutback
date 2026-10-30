"""Approved runtime scope and one-shot check intents. No policy/request content."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


def now():
    return datetime.now(UTC)


class GuardrailPolicyRow(Base):
    __tablename__ = "runtime_guardrail_policies"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_runtime_guardrail_scope"),
        ForeignKeyConstraint(
            ["capture_policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_runtime_guardrail_capture_scope",
        ),
        CheckConstraint("state IN ('PENDING','APPROVED','DISABLED')", name="ck_guardrail_state"),
        CheckConstraint("state_version BETWEEN 0 AND 2", name="ck_guardrail_state_version"),
        CheckConstraint(
            "max_checks BETWEEN 2 AND 10000000 AND checks_used BETWEEN 0 AND max_checks",
            name="ck_guardrail_shared_checks",
        ),
        Index("ix_runtime_guardrail_project", "project_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    capture_policy_id: Mapped[UUID] = mapped_column(Uuid)
    # The pointer may survive retention. A missing payload never restores authority.
    payload_id: Mapped[UUID] = mapped_column(Uuid)
    binding: Mapped[dict] = mapped_column(JSON)
    approval_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(12), default="PENDING")
    state_version: Mapped[int] = mapped_column(BigInteger, default=0)
    max_checks: Mapped[int] = mapped_column(BigInteger)
    checks_used: Mapped[int] = mapped_column(BigInteger, default=0)
    created_by: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approval_ref: Mapped[UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GuardrailOperationRow(Base):
    __tablename__ = "runtime_guardrail_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["runtime_guardrail_policies.id", "runtime_guardrail_policies.project_id"],
            ondelete="CASCADE",
            name="fk_runtime_guardrail_operation_scope",
        ),
        CheckConstraint(
            "state IN ('RESERVED','COMPLETED','UNKNOWN')", name="ck_guardrail_check_state"
        ),
        CheckConstraint(
            "stage IN ('input','output','tool_input','tool_output')",
            name="ck_guardrail_check_stage",
        ),
        Index("ix_guardrail_check_project_policy", "project_id", "policy_id", "created_at"),
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    policy_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    stage: Mapped[str] = mapped_column(String(12), primary_key=True)
    state: Mapped[str] = mapped_column(String(12), default="RESERVED")
    decision: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
