"""Content-free dataset family identity and durable exposure receipts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class DatasetFamily(Base):
    __tablename__ = "dataset_families"
    __table_args__ = (Index("ix_dataset_family_canonical", "project_id", "canonical_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    canonical_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_families.id", ondelete="CASCADE")
    )


class DatasetAlias(Base):
    __tablename__ = "dataset_identity_aliases"
    __table_args__ = (
        UniqueConstraint("project_id", "token_hash", name="uq_dataset_identity_alias"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(80))
    family_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_families.id", ondelete="CASCADE"))


class DatasetExposure(Base):
    __tablename__ = "dataset_exposures"
    __table_args__ = (
        UniqueConstraint("project_id", "family_id", "source_ref", name="uq_dataset_exposure"),
        Index("ix_dataset_exposure_family", "project_id", "family_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    family_id: Mapped[UUID] = mapped_column(ForeignKey("dataset_families.id", ondelete="CASCADE"))
    dataset_id: Mapped[UUID | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("experiment_runs.id", ondelete="SET NULL")
    )
    source_ref: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20))
    protected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
