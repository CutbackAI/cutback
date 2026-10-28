"""Versioned content-free task outcomes and externally asserted cost allocations."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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


class TaskRecord(Base):
    __tablename__ = "application_tasks"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "environment_id", name="uq_task_record_scope"),
        UniqueConstraint(
            "project_id", "environment_id", "task_id", name="uq_task_external_identity"
        ),
        UniqueConstraint(
            "project_id", "environment_id", "trace_id", name="uq_task_trace_allocation"
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_task_environment_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "current_revision >= 0 AND current_revision <= 100", name="ck_task_revision"
        ),
        Index("ix_task_scope_time", "project_id", "environment_id", "occurred_at", "id"),
        Index(
            "ix_task_release_time",
            "project_id",
            "environment_id",
            "workflow",
            "release",
            "occurred_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    task_id: Mapped[UUID] = mapped_column(Uuid)
    trace_id: Mapped[str] = mapped_column(String(32))
    workflow: Mapped[str] = mapped_column(String(200))
    service: Mapped[str] = mapped_column(String(200))
    release: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TaskResult(Base):
    __tablename__ = "application_task_results"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "environment_id", "result_id", name="uq_task_result_external_identity"
        ),
        UniqueConstraint("task_record_id", "revision", name="uq_task_result_revision"),
        ForeignKeyConstraint(
            ["task_record_id", "project_id", "environment_id"],
            [
                "application_tasks.id",
                "application_tasks.project_id",
                "application_tasks.environment_id",
            ],
            name="fk_task_result_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("revision >= 1 AND revision <= 100", name="ck_task_result_revision"),
        Index("ix_task_result_scope_time", "project_id", "environment_id", "received_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    task_record_id: Mapped[UUID] = mapped_column(Uuid)
    result_id: Mapped[UUID] = mapped_column(Uuid)
    revision: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    metadata_record: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TaskCostSnapshot(Base):
    __tablename__ = "task_cost_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "environment_id", name="uq_task_snapshot_scope"),
        UniqueConstraint("project_id", "snapshot_id", name="uq_task_snapshot_request"),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_task_snapshot_environment_scope",
            ondelete="CASCADE",
        ),
        Index("ix_task_snapshot_scope_time", "project_id", "environment_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ImprovementObservation(Base):
    __tablename__ = "improvement_observations"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "environment_id", name="uq_improvement_scope"),
        UniqueConstraint("project_id", "observation_id", name="uq_improvement_observation_request"),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_improvement_environment_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["change_id", "project_id"],
            ["change_proposals.id", "change_proposals.project_id"],
            name="fk_improvement_change_scope",
        ),
        ForeignKeyConstraint(
            ["baseline_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_improvement_baseline_scope",
        ),
        ForeignKeyConstraint(
            ["candidate_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_improvement_candidate_scope",
        ),
        CheckConstraint(
            "baseline_snapshot_id != candidate_snapshot_id",
            name="ck_improvement_distinct_snapshots",
        ),
        Index("ix_improvement_scope_time", "project_id", "environment_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    observation_id: Mapped[UUID] = mapped_column(Uuid)
    change_id: Mapped[UUID | None] = mapped_column(Uuid)
    baseline_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    candidate_snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    receipt: Mapped[dict] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
