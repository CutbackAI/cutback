"""Content-free episode state and immutable one-shot native call accounting."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
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


class ExecutionEpisode(Base):
    __tablename__ = "execution_episodes"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "model_key", "case_key", "repeat_index", name="uq_execution_episode_case"
        ),
        UniqueConstraint(
            "id", "environment_id", "run_id", "project_id", name="uq_execution_episode_scope"
        ),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            ondelete="CASCADE",
            name="fk_episode_run_scope",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_episode_environment_scope",
        ),
        CheckConstraint("repeat_index >= 0 AND repeat_index < 5", name="ck_episode_repeat"),
        CheckConstraint(
            "max_requests >= 1 AND max_requests <= 36 AND requests_used <= max_requests",
            name="ck_episode_turn_budget",
        ),
        CheckConstraint(
            "requests_used >= 0 AND known_cost_requests >= 0 "
            "AND known_cost_requests <= requests_used",
            name="ck_episode_request_counts",
        ),
        CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0", name="ck_episode_money"),
        Index("ix_execution_episode_run", "project_id", "run_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    model_key: Mapped[str] = mapped_column(String(200))
    # Per-run opaque digest; never store a user-provided case name or question here.
    case_key: Mapped[str] = mapped_column(String(64))
    repeat_index: Mapped[int] = mapped_column(Integer)
    execution_version: Mapped[str] = mapped_column(String(50))
    max_requests: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30), default="running")
    outcome: Mapped[str | None] = mapped_column(String(30))
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    known_cost_requests: Mapped[int] = mapped_column(Integer, default=0)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExecutionCall(Base):
    __tablename__ = "execution_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["provider_batch_id", "project_id", "run_id"],
            ["provider_batches.id", "provider_batches.project_id", "provider_batches.run_id"],
            ondelete="CASCADE",
            name="fk_execution_call_provider_batch_scope",
        ),
        Index("ix_execution_call_episode_purpose", "project_id", "run_id", "episode_id", "purpose"),
        UniqueConstraint("run_id", "sequence", name="uq_execution_call_sequence"),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            ondelete="CASCADE",
            name="fk_execution_call_run_scope",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_execution_call_environment_scope",
        ),
        ForeignKeyConstraint(
            ["episode_id", "environment_id", "run_id", "project_id"],
            [
                "execution_episodes.id",
                "execution_episodes.environment_id",
                "execution_episodes.run_id",
                "execution_episodes.project_id",
            ],
            ondelete="CASCADE",
            name="fk_execution_call_episode_scope",
        ),
        CheckConstraint("sequence > 0 AND sequence <= 10000", name="ck_execution_call_sequence"),
        CheckConstraint(
            "reserved_usd >= 0 AND (spent_usd IS NULL OR spent_usd >= 0)",
            name="ck_execution_call_money",
        ),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0)",
            name="ck_execution_call_tokens",
        ),
        Index("ix_execution_call_run", "project_id", "run_id", "sequence"),
        Index("ix_execution_call_lease", "project_id", "state", "lease_until"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    episode_id: Mapped[UUID | None] = mapped_column(Uuid)
    provider_batch_id: Mapped[UUID | None] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    model_key: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    response_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cache_observation: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    purpose: Mapped[str] = mapped_column(String(30))
    price_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="reserved")
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    spent_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    complete: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
