"""Online content evaluation: immutable authority, one-shot costs and safe score metadata."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class QualityRule(Base):
    __tablename__ = "quality_rules"
    __table_args__ = (
        UniqueConstraint("project_id", "key", "revision", name="uq_quality_rule_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    online_rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("online_rules.id", ondelete="CASCADE"), unique=True
    )
    key: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict] = mapped_column(JSON)
    prompt_hash: Mapped[str | None] = mapped_column(String(64))
    approval_hash: Mapped[str] = mapped_column(String(64))
    creator: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    evaluations_used: Mapped[int] = mapped_column(Integer, default=0)
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    known_cost_requests: Mapped[int] = mapped_column(Integer, default=0)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    backfill_cursor: Mapped[UUID | None] = mapped_column(Uuid)
    backfill_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_served_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class QualityEvaluation(Base):
    __tablename__ = "quality_evaluations"
    __table_args__ = (
        UniqueConstraint("rule_id", "span_record_id", name="uq_quality_target"),
        Index("ix_quality_queue", "rule_id", "state", "available_at"),
        Index("ix_quality_cohort", "project_id", "rule_id", "occurred_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("quality_rules.id", ondelete="CASCADE"))
    span_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_spans.id", ondelete="SET NULL")
    )
    trace_id: Mapped[str] = mapped_column(String(32))
    span_id: Mapped[str] = mapped_column(String(16))
    source_metadata: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    evaluation_reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    charge_started: Mapped[bool] = mapped_column(Boolean, default=False)
    output_reserved: Mapped[bool] = mapped_column(Boolean, default=False)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    spent_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    output_payload_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payload_samples.id", ondelete="SET NULL")
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QualityPromptUse(Base):
    __tablename__ = "quality_prompt_uses"
    __table_args__ = (UniqueConstraint("rule_id", "version_id", name="uq_quality_prompt_use"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("quality_rules.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id", ondelete="CASCADE"))
