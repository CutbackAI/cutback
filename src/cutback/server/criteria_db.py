"""Generated criteria lifecycle and exact experiment uses, without task content."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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


class CriteriaVersionRecord(Base):
    __tablename__ = "evaluation_criteria_versions"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_criteria_version_project"),
        ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            name="fk_criteria_dataset_scope",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_criteria_environment_scope",
        ),
        ForeignKeyConstraint(
            ["capture_policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_criteria_capture_scope",
        ),
        ForeignKeyConstraint(
            ["generation_plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            name="fk_criteria_plan_scope",
        ),
        ForeignKeyConstraint(
            ["generation_run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_criteria_run_scope",
        ),
        CheckConstraint(
            "state IN ('PENDING','QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_criteria_generation_state",
        ),
        CheckConstraint(
            "review_status IN ('PENDING','APPROVED','REJECTED')", name="ck_criteria_review_state"
        ),
        CheckConstraint(
            "state != 'COMPLETED' OR (generation_run_id IS NOT NULL "
            "AND output_payload_id IS NOT NULL AND version_sha256 IS NOT NULL)",
            name="ck_criteria_completed_proposal",
        ),
        CheckConstraint(
            "review_status = 'PENDING' OR (state = 'COMPLETED' "
            "AND version_sha256 IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_approval_id IS NOT NULL)",
            name="ck_criteria_review_requires_completed_content",
        ),
        Index("ix_criteria_project_created", "project_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    dataset_id: Mapped[UUID] = mapped_column(Uuid)
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    capture_policy_id: Mapped[UUID] = mapped_column(Uuid)
    generation_plan_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    generation_run_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    # Pointers survive expiry; every dereference checks the actual tenant/policy/hash.
    input_payload_id: Mapped[UUID] = mapped_column(Uuid)
    output_payload_id: Mapped[UUID | None] = mapped_column(Uuid)
    source_dataset_sha256: Mapped[str] = mapped_column(String(64))
    generation_plan_sha256: Mapped[str] = mapped_column(String(64))
    version_sha256: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(12), default="PENDING")
    review_status: Mapped[str] = mapped_column(String(12), default="PENDING")
    created_by: Mapped[str] = mapped_column(String(100))
    generation_approved_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    review_approval_id: Mapped[UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CriteriaExperimentUse(Base):
    __tablename__ = "evaluation_criteria_uses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["criteria_id", "project_id"],
            ["evaluation_criteria_versions.id", "evaluation_criteria_versions.project_id"],
            name="fk_criteria_use_version_scope",
        ),
        ForeignKeyConstraint(
            ["plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            ondelete="CASCADE",
            name="fk_criteria_use_plan_scope",
        ),
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    plan_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    criteria_id: Mapped[UUID] = mapped_column(Uuid)
    version_sha256: Mapped[str] = mapped_column(String(64))
    review_approval_id: Mapped[UUID] = mapped_column(Uuid)
