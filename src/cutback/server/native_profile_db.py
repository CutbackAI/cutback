"""One-shot, scoped encrypted-workload analysis jobs; summaries contain no fingerprints."""

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


class NativeProfileAnalysis(Base):
    __tablename__ = "native_profile_analyses"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_native_profile_analysis_project"),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_native_profile_analysis_policy",
        ),
        CheckConstraint(
            "state IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED','EXPIRED')",
            name="ck_native_profile_analysis_state",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= 1 AND max_observations >= 1 AND "
            "max_observations <= 1000 AND max_wall_seconds >= 5 AND max_wall_seconds <= 300",
            name="ck_native_profile_analysis_bounds",
        ),
        Index("ix_native_profile_analysis_queue", "state", "created_at", "id"),
        Index(
            "ix_native_profile_analysis_scope",
            "project_id",
            "environment",
            "workflow",
            "created_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    environment: Mapped[str] = mapped_column(String(200))
    workflow: Mapped[str] = mapped_column(String(200))
    requester_actor: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(200))
    request_sha256: Mapped[str] = mapped_column(String(64))
    capture_policy_sha256: Mapped[str] = mapped_column(String(64))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_observations: Mapped[int] = mapped_column(Integer)
    max_wall_seconds: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(30), default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[dict | None] = mapped_column(JSON)
