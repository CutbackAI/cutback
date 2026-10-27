"""Content-free review workflow state; judgments and rubrics live in encrypted payloads."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class ReviewQueue(Base):
    __tablename__ = "review_queues"
    __table_args__ = (Index("ix_review_queue_project", "project_id", "created_at"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("capture_policies.id"))
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    version_hash: Mapped[str] = mapped_column(String(64))
    workflow: Mapped[str] = mapped_column(String(200))
    environment: Mapped[str] = mapped_column(String(200))
    required_reviewers: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    promoted_dataset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL")
    )


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("queue_id", "source_kind", "source_id", name="uq_review_item_source"),
        Index("ix_review_item_state", "queue_id", "state", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    queue_id: Mapped[UUID] = mapped_column(ForeignKey("review_queues.id", ondelete="CASCADE"))
    source_kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[UUID] = mapped_column(Uuid)
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    state: Mapped[str] = mapped_column(String(20), default="ready")


class ReviewLease(Base):
    __tablename__ = "review_leases"
    __table_args__ = (Index("ix_review_lease_item", "item_id", "expires_at"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("review_items.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")


class ReviewVote(Base):
    __tablename__ = "review_votes"
    __table_args__ = (UniqueConstraint("item_id", "actor", name="uq_review_vote_actor"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("review_items.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(64))
    lease_id: Mapped[UUID] = mapped_column(ForeignKey("review_leases.id"))
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    # Deliberately no public label/decision hash: low-cardinality labels are guessable.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ReviewResolution(Base):
    __tablename__ = "review_resolutions"
    __table_args__ = (UniqueConstraint("item_id", name="uq_review_resolution"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    item_id: Mapped[UUID] = mapped_column(ForeignKey("review_items.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(20))
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
