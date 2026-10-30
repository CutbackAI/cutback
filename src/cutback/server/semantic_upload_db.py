"""Scoped resumable dataset upload metadata; content uses registered encrypted payloads."""

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


class SemanticDatasetUpload(Base):
    __tablename__ = "semantic_dataset_uploads"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_semantic_upload_project"),
        ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_namespace",
        ),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_policy",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_dataset",
        ),
        CheckConstraint(
            "expected_cases >= 1 AND expected_cases <= 512 AND received_cases >= 0 "
            "AND received_cases <= expected_cases AND max_bytes >= 1 AND max_bytes <= 33554432 "
            "AND received_bytes >= 0 AND received_bytes <= max_bytes",
            name="ck_semantic_upload_bounds",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= 2 AND uncertain_attempts >= 0 "
            "AND uncertain_attempts <= attempts",
            name="ck_semantic_upload_attempts",
        ),
        Index("ix_semantic_upload_policy_expiry", "policy_id", "state", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    namespace_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    dataset_id: Mapped[UUID | None] = mapped_column(Uuid)
    specification: Mapped[dict] = mapped_column(JSON)
    approval_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="STAGING")
    expected_cases: Mapped[int] = mapped_column(Integer)
    received_cases: Mapped[int] = mapped_column(Integer, default=0)
    max_bytes: Mapped[int] = mapped_column(Integer)
    received_bytes: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    uncertain_attempts: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(120))
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SemanticUploadCase(Base):
    __tablename__ = "semantic_upload_cases"
    __table_args__ = (
        UniqueConstraint("upload_id", "ordinal", name="uq_semantic_upload_ordinal"),
        ForeignKeyConstraint(
            ["upload_id", "project_id"],
            ["semantic_dataset_uploads.id", "semantic_dataset_uploads.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_case_parent",
        ),
        ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_semantic_upload_case_payload",
        ),
        CheckConstraint(
            "ordinal >= 0 AND ordinal < 512 AND byte_count > 0 AND byte_count <= 1048576",
            name="ck_semantic_upload_case_bound",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    upload_id: Mapped[UUID] = mapped_column(Uuid)
    ordinal: Mapped[int] = mapped_column(Integer)
    payload_id: Mapped[UUID] = mapped_column(Uuid)
    content_hash: Mapped[str] = mapped_column(String(64))
    byte_count: Mapped[int] = mapped_column(Integer)
