"""Content-free one-shot isolated sessions and operations in the canonical run."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class ToolSessionRecord(Base):
    __tablename__ = "tool_sessions"
    __table_args__ = (
        UniqueConstraint(
            "id", "environment_id", "run_id", "project_id", name="uq_tool_session_scope"
        ),
        UniqueConstraint("episode_id", name="uq_tool_session_episode"),
        ForeignKeyConstraint(
            ["episode_id", "environment_id", "run_id", "project_id"],
            [
                "execution_episodes.id",
                "execution_episodes.environment_id",
                "execution_episodes.run_id",
                "execution_episodes.project_id",
            ],
            ondelete="CASCADE",
            name="fk_tool_session_episode_scope",
        ),
        CheckConstraint(
            "max_calls >= 1 AND max_calls <= 100 AND calls_used >= 0 AND calls_used <= max_calls",
            name="ck_tool_session_call_bound",
        ),
        CheckConstraint(
            "max_wall_seconds >= 1 AND max_wall_seconds <= 120", name="ck_tool_session_wall_bound"
        ),
        CheckConstraint(
            "state IN ('STARTING','READY','UNCERTAIN','CLOSED')", name="ck_tool_session_state"
        ),
        CheckConstraint(
            "cleanup_attempts >= 0 AND cleanup_attempts <= 3", name="ck_tool_cleanup_attempts"
        ),
        Index("ix_tool_session_project_run", "project_id", "run_id", "id"),
        Index("ix_tool_session_cleanup", "project_id", "cleanup_confirmed", "lease_until"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(Uuid)
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    episode_id: Mapped[UUID] = mapped_column(Uuid)
    principal_id: Mapped[UUID] = mapped_column(Uuid)
    broker_id: Mapped[UUID] = mapped_column(Uuid)
    environment_name: Mapped[str] = mapped_column(String(200))
    workflow: Mapped[str] = mapped_column(String(200))
    broker_generation: Mapped[UUID] = mapped_column(Uuid)
    policy_sha256: Mapped[str] = mapped_column(String(64))
    approval_hash: Mapped[str] = mapped_column(String(64))
    image_id: Mapped[str] = mapped_column(String(71))
    max_calls: Mapped[int] = mapped_column(Integer)
    max_wall_seconds: Mapped[int] = mapped_column(Integer)
    calls_used: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(20), default="STARTING")
    cleanup_confirmed: Mapped[bool] = mapped_column(default=False)
    cleanup_requested: Mapped[bool] = mapped_column(default=False)
    cleanup_attempts: Mapped[int] = mapped_column(Integer, default=0)
    cleanup_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolOperationRecord(Base):
    __tablename__ = "tool_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["session_id", "environment_id", "run_id", "project_id"],
            [
                "tool_sessions.id",
                "tool_sessions.environment_id",
                "tool_sessions.run_id",
                "tool_sessions.project_id",
            ],
            ondelete="CASCADE",
            name="fk_tool_operation_session_scope",
        ),
        UniqueConstraint("session_id", "sequence", name="uq_tool_operation_sequence"),
        CheckConstraint("sequence >= 1 AND sequence <= 100", name="ck_tool_operation_sequence"),
        CheckConstraint(
            "state IN ('DISPATCHING','RETURNED','DENIED','UNCERTAIN')",
            name="ck_tool_operation_state",
        ),
        CheckConstraint(
            "tool_kind IN ('records_read','records_patch','unapproved')",
            name="ck_tool_operation_kind",
        ),
        Index("ix_tool_operation_project_run", "project_id", "run_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid)
    project_id: Mapped[UUID] = mapped_column(Uuid)
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    tool_kind: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default="DISPATCHING")
    state_changed: Mapped[bool | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
