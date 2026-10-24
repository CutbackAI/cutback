"""Shared OSS/hosted control records. No external service or license dependency."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


def now():
    return datetime.now(UTC)


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("organization_id", "issuer", "subject_hash"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    issuer: Mapped[str] = mapped_column(String(500))
    subject_hash: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectGrant(Base):
    __tablename__ = "project_grants"
    __table_args__ = (UniqueConstraint("member_id", "project_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    member_id: Mapped[UUID] = mapped_column(
        ForeignKey("organization_members.id", ondelete="CASCADE")
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(30))


class OIDCChallenge(Base):
    __tablename__ = "oidc_challenges"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    pkce_challenge: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class ProjectSettings(Base):
    __tablename__ = "project_settings"
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    raw_days: Mapped[int] = mapped_column(Integer, default=30)
    aggregate_days: Mapped[int] = mapped_column(Integer, default=395)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    customer_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    subscription_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(40), default="unconfigured")
    price_id: Mapped[str | None] = mapped_column(String(200))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    monthly_event_cap: Mapped[int | None] = mapped_column(Integer)
    hosted_spend_cap_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))


class BillingEvent(Base):
    __tablename__ = "billing_events"
    event_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class HostedUsage(Base):
    __tablename__ = "hosted_usage"
    __table_args__ = (UniqueConstraint("organization_id", "period"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    period: Mapped[str] = mapped_column(String(10))
    accepted_events: Mapped[int] = mapped_column(Integer, default=0)
    reserved_hosted_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))


class ExperimentSchedule(Base):
    __tablename__ = "experiment_schedules"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_plans.id"))
    specification: Mapped[dict] = mapped_column(JSON)
    approval_hash: Mapped[str] = mapped_column(String(64))
    approval_ref: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    runs_queued: Mapped[int] = mapped_column(Integer, default=0)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    approved_by: Mapped[str] = mapped_column(String(100))


class RolloutRecord(Base):
    __tablename__ = "rollouts"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    recommendation_id: Mapped[UUID] = mapped_column(ForeignKey("recommendations.id"))
    status: Mapped[str] = mapped_column(String(40), default="PLANNED")
    specification: Mapped[dict] = mapped_column(JSON)
    observations: Mapped[list] = mapped_column(JSON, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TrainingJob(Base):
    __tablename__ = "training_jobs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"))
    specification: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    approval_hash: Mapped[str] = mapped_column(String(64))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CacheEntry(Base):
    __tablename__ = "cache_entries"
    __table_args__ = (UniqueConstraint("project_id", "key_hash"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("capture_policies.id"))
    key_hash: Mapped[str] = mapped_column(String(64))
    object_ref: Mapped[str] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
