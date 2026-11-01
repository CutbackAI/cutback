"""Content-free logical policy operations, separate from provider billing records."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
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


class PolicyActivityRecord(Base):
    __tablename__ = "source_policy_activity"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "environment_id", "event_id", name="uq_policy_activity_event"
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_policy_activity_environment",
        ),
        Index(
            "ix_policy_activity_scope",
            "project_id",
            "environment_id",
            "workflow",
            "service_name",
            "source_commit",
            "call_site_id",
            "policy_sha256",
            "started_ns",
            "event_id",
        ),
        Index("ix_policy_activity_retention", "project_id", "received_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    workflow: Mapped[str] = mapped_column(String(200))
    service_name: Mapped[str] = mapped_column(String(200))
    source_commit: Mapped[str] = mapped_column(String(64))
    call_site_id: Mapped[str] = mapped_column(String(200))
    policy_sha256: Mapped[str] = mapped_column(String(64))
    started_ns: Mapped[int] = mapped_column(BigInteger)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    metadata_record: Mapped[dict] = mapped_column(JSON)
