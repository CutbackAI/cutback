"""Incident collaboration metadata; all comment bodies live in encrypted payloads."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import (
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


class IncidentComment(Base):
    __tablename__ = "incident_comments"
    __table_args__ = (
        CheckConstraint("revision BETWEEN 1 AND 51", name="ck_incident_comment_revision"),
        CheckConstraint(
            "(deleted = false AND deleted_by IS NULL AND current_version_id IS NOT NULL) OR "
            "(deleted = true AND deleted_by IS NOT NULL AND current_version_id IS NULL)",
            name="ck_incident_comment_tombstone",
        ),
        UniqueConstraint("id", "project_id", name="uq_incident_comment_scope"),
        ForeignKeyConstraint(
            ["incident_id", "project_id"],
            ["incidents.id", "incidents.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_parent",
        ),
        Index("ix_incident_comment_page", "project_id", "incident_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    incident_id: Mapped[UUID] = mapped_column(Uuid)
    creator: Mapped[str] = mapped_column(String(100))
    revision: Mapped[int] = mapped_column(Integer)
    current_version_id: Mapped[UUID | None] = mapped_column(Uuid)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IncidentCommentVersion(Base):
    __tablename__ = "incident_comment_versions"
    __table_args__ = (
        CheckConstraint("revision BETWEEN 1 AND 50", name="ck_incident_comment_version_revision"),
        UniqueConstraint("comment_id", "revision", name="uq_incident_comment_revision"),
        ForeignKeyConstraint(
            ["comment_id", "project_id"],
            ["incident_comments.id", "incident_comments.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_version_parent",
        ),
        ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_version_payload",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    comment_id: Mapped[UUID] = mapped_column(Uuid)
    payload_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
