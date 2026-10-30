"""Content-free asynchronous batch control linked to the canonical execution ledger."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

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


class ProviderBatchRecord(Base):
    __tablename__ = "provider_batches"
    __table_args__ = (
        UniqueConstraint("project_id", "plan_id", name="uq_provider_batch_plan"),
        UniqueConstraint("id", "project_id", "run_id", name="uq_provider_batch_run_identity"),
        ForeignKeyConstraint(
            ["plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_plan_scope",
        ),
        ForeignKeyConstraint(
            ["job_id", "project_id", "run_id"],
            ["jobs.id", "jobs.project_id", "jobs.run_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_job_scope",
        ),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_run_scope",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_environment_scope",
        ),
        CheckConstraint(
            "json_typeof(pieces) = 'array' AND json_array_length(pieces) <= 6",
            name="ck_provider_batch_piece_bound",
        ),
        CheckConstraint(
            "json_typeof(calls) = 'array' AND json_array_length(calls) <= 1000",
            name="ck_provider_batch_call_bound",
        ),
        CheckConstraint(
            "active_elapsed_us >= 0 AND active_reserved_us >= 0 "
            "AND active_reserved_us <= 180000000 AND active_uncertain_us >= 0",
            name="ck_provider_batch_active_wall",
        ),
        Index("ix_provider_batch_eligibility", "state", "available_at", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    plan_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID | None] = mapped_column(Uuid)
    job_id: Mapped[UUID | None] = mapped_column(Uuid)
    approval_hash: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="PENDING")
    actor: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    pieces: Mapped[list] = mapped_column(JSON, default=list)
    calls: Mapped[list] = mapped_column(JSON, default=list)
    active_elapsed_us: Mapped[int] = mapped_column(BigInteger, default=0)
    active_reserved_us: Mapped[int] = mapped_column(BigInteger, default=0)
    active_uncertain_us: Mapped[int] = mapped_column(BigInteger, default=0)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
