"""Scoped outcome policies and bounded observation receipts; no application content."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class OutcomeWatch(Base):
    __tablename__ = "outcome_watches"
    __table_args__ = (
        UniqueConstraint("project_id", "watch_id", name="uq_outcome_watch_request"),
        UniqueConstraint("id", "project_id", "environment_id", name="uq_outcome_watch_scope"),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_outcome_watch_environment_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "windows_processed >= 0 AND windows_processed <= 100 "
            "AND last_window_index >= -1 AND last_window_index <= 1000000 "
            "AND windows_skipped >= 0 AND windows_skipped <= 1000000",
            name="ck_outcome_watch_window_bounds",
        ),
        CheckConstraint(
            "status IN ('PENDING','APPROVED','PAUSED','EXPIRED','EXHAUSTED','BLOCKED') "
            "AND (NOT enabled OR status = 'APPROVED')",
            name="ck_outcome_watch_state",
        ),
        Index("ix_outcome_watch_due", "enabled", "next_run_at", "project_id", "id"),
        Index("ix_outcome_watch_scope_time", "project_id", "environment_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    watch_id: Mapped[UUID] = mapped_column(Uuid)
    approval_hash: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approval_ref: Mapped[str | None] = mapped_column(String(200))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_window_index: Mapped[int] = mapped_column(Integer, default=-1)
    windows_processed: Mapped[int] = mapped_column(Integer, default=0)
    windows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    last_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OutcomeWatchWindow(Base):
    __tablename__ = "outcome_watch_windows"
    __table_args__ = (
        UniqueConstraint("watch_record_id", "window_index", name="uq_outcome_watch_window"),
        ForeignKeyConstraint(
            ["watch_record_id", "project_id", "environment_id"],
            ["outcome_watches.id", "outcome_watches.project_id", "outcome_watches.environment_id"],
            name="fk_outcome_watch_window_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["candidate_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_outcome_watch_window_snapshot_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["improvement_id", "project_id", "environment_id"],
            [
                "improvement_observations.id",
                "improvement_observations.project_id",
                "improvement_observations.environment_id",
            ],
            name="fk_outcome_watch_window_improvement_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "window_index >= 0 AND window_index <= 1000000 AND window_start < window_end",
            name="ck_outcome_watch_window_time",
        ),
        Index(
            "ix_outcome_watch_window_page",
            "project_id",
            "environment_id",
            "watch_record_id",
            "window_index",
        ),
        Index("ix_outcome_watch_window_snapshot", "candidate_snapshot_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    watch_record_id: Mapped[UUID] = mapped_column(Uuid)
    window_index: Mapped[int] = mapped_column(Integer)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    candidate_snapshot_id: Mapped[UUID | None] = mapped_column(Uuid)
    improvement_id: Mapped[UUID | None] = mapped_column(Uuid)
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
