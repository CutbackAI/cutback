"""Content-free application gateway policies, virtual keys and spend reservations."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


def now():
    return datetime.now(UTC)


class GatewayPolicyRow(Base):
    __tablename__ = "gateway_policies"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_gateway_policy_scope"),
        UniqueConstraint("project_id", "policy_id", name="uq_gateway_policy_request"),
        CheckConstraint("expires_at > starts_at", name="ck_gateway_policy_window"),
        CheckConstraint(
            "state IN ('PENDING','APPROVED','DISABLED','BLOCKED')", name="ck_gateway_policy_state"
        ),
        Index("ix_gateway_policy_project_window", "project_id", "starts_at", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    specification: Mapped[dict] = mapped_column(JSON)
    approval_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="PENDING")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approval_ref: Mapped[UUID | None] = mapped_column(Uuid)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GatewayKeyRow(Base):
    __tablename__ = "gateway_keys"
    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_gateway_key_request"),
        UniqueConstraint(
            "id", "policy_id", "project_id", "environment_id", name="uq_gateway_key_scope"
        ),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["gateway_policies.id", "gateway_policies.project_id"],
            ondelete="CASCADE",
            name="fk_gateway_key_policy_scope",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_gateway_key_environment_scope",
        ),
        Index("ix_gateway_key_project_policy", "project_id", "policy_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    team_id: Mapped[UUID] = mapped_column(Uuid)
    specification: Mapped[dict] = mapped_column(JSON)
    issued_by: Mapped[str] = mapped_column(String(100))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class GatewayBudgetRow(Base):
    __tablename__ = "gateway_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["gateway_policies.id", "gateway_policies.project_id"],
            ondelete="CASCADE",
            name="fk_gateway_budget_policy_scope",
        ),
        CheckConstraint(
            "scope_kind IN ('project','team','model','key')", name="ck_gateway_budget_kind"
        ),
        CheckConstraint(
            "spent_usd >= 0 AND reserved_usd >= 0 AND requests_used >= 0 "
            "AND active_requests >= 0 AND uncertain_requests >= 0 "
            "AND active_requests + uncertain_requests <= requests_used",
            name="ck_gateway_budget_nonnegative",
        ),
        Index("ix_gateway_budget_project_policy", "project_id", "policy_id"),
    )
    policy_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    scope_kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    requests_used: Mapped[int] = mapped_column(BigInteger, default=0)
    active_requests: Mapped[int] = mapped_column(BigInteger, default=0)
    uncertain_requests: Mapped[int] = mapped_column(BigInteger, default=0)


class GatewayCallRow(Base):
    __tablename__ = "gateway_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["key_id", "policy_id", "project_id", "environment_id"],
            [
                "gateway_keys.id",
                "gateway_keys.policy_id",
                "gateway_keys.project_id",
                "gateway_keys.environment_id",
            ],
            ondelete="CASCADE",
            name="fk_gateway_call_key_scope",
        ),
        CheckConstraint(
            "state IN ('DISPATCHED','UNCERTAIN','SETTLED')", name="ck_gateway_call_state"
        ),
        CheckConstraint("max_output_tokens BETWEEN 16 AND 16384", name="ck_gateway_call_output"),
        CheckConstraint(
            "traffic_kind IN ('application','evaluation','judge',"
            "'optimization','simulation','unknown')",
            name="ck_gateway_call_purpose",
        ),
        CheckConstraint(
            "reserved_usd > 0 AND (cost_usd IS NULL OR cost_usd >= 0) "
            "AND ((state = 'SETTLED' AND cost_usd IS NOT NULL) "
            "OR (state <> 'SETTLED' AND cost_usd IS NULL))",
            name="ck_gateway_call_money",
        ),
        Index("ix_gateway_call_lease", "state", "lease_until", "project_id"),
        Index(
            "ix_gateway_call_policy_time", "project_id", "policy_id", "created_at", "operation_id"
        ),
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    operation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    key_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    team_id: Mapped[UUID] = mapped_column(Uuid)
    model_key: Mapped[str] = mapped_column(String(100))
    request_hmac: Mapped[str] = mapped_column(String(64))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    traffic_kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="DISPATCHED")
    price_sha256: Mapped[str] = mapped_column(String(64))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    code: Mapped[str | None] = mapped_column(String(100))
    event_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
