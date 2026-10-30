"""Scoped semantic-cache metadata; supplied vectors and answers stay encrypted."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class SemanticCacheNamespace(Base):
    __tablename__ = "semantic_cache_namespaces"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_semantic_namespace_project"),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_policy",
        ),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_environment",
        ),
        ForeignKeyConstraint(
            ["serving_key_id", "project_id", "environment_id"],
            ["access_keys.id", "access_keys.project_id", "access_keys.environment_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_key",
        ),
        ForeignKeyConstraint(
            ["active_qualification_id", "project_id", "id"],
            [
                "semantic_cache_qualifications.id",
                "semantic_cache_qualifications.project_id",
                "semantic_cache_qualifications.namespace_id",
            ],
            name="fk_semantic_namespace_active_qualification",
            use_alter=True,
        ),
        Index("ix_semantic_namespace_project_created", "project_id", "created_at", "id"),
        CheckConstraint(
            "state IN ('DRAFT','ACTIVE','REVOKED') AND "
            "((state = 'ACTIVE') = (active_qualification_id IS NOT NULL)) AND "
            "((state = 'ACTIVE') = (active_approval_hash IS NOT NULL))",
            name="ck_semantic_namespace_activation",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    serving_key_id: Mapped[UUID] = mapped_column(Uuid)
    specification: Mapped[dict] = mapped_column(JSON)
    specification_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="DRAFT")
    active_qualification_id: Mapped[UUID | None] = mapped_column(Uuid)
    active_approval_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SemanticCacheMember(Base):
    __tablename__ = "semantic_cache_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cache_entry_id", "project_id"],
            ["cache_entries.id", "cache_entries.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_member_entry",
        ),
        ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_member_namespace",
        ),
        Index("ix_semantic_member_namespace", "project_id", "namespace_id", "cache_entry_id"),
    )
    cache_entry_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    namespace_id: Mapped[UUID] = mapped_column(Uuid)
    content_hash: Mapped[str] = mapped_column(String(64))


class SemanticCacheQualification(Base):
    __tablename__ = "semantic_cache_qualifications"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_semantic_qualification_project"),
        UniqueConstraint(
            "id", "project_id", "namespace_id", name="uq_semantic_qualification_namespace"
        ),
        ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_qualification_namespace",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_qualification_dataset",
        ),
        Index("ix_semantic_qualification_eligibility", "state", "created_at", "id"),
        CheckConstraint(
            "json_typeof(catalog_manifest) = 'array' "
            "AND json_array_length(catalog_manifest) <= 256",
            name="ck_semantic_qualification_catalog_bound",
        ),
        CheckConstraint(
            "active_elapsed_us >= 0 AND active_reserved_us >= 0 "
            "AND active_reserved_us <= 120000000 AND active_uncertain_us >= 0",
            name="ck_semantic_qualification_active_wall",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    namespace_id: Mapped[UUID] = mapped_column(Uuid)
    dataset_id: Mapped[UUID] = mapped_column(Uuid)
    specification: Mapped[dict] = mapped_column(JSON)
    approval_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(30), default="PENDING")
    approved_by: Mapped[str | None] = mapped_column(String(100))
    selected_threshold: Mapped[float | None] = mapped_column(Float)
    catalog_manifest: Mapped[list] = mapped_column(JSON, default=list)
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_elapsed_us: Mapped[int] = mapped_column(BigInteger, default=0)
    active_reserved_us: Mapped[int] = mapped_column(BigInteger, default=0)
    active_uncertain_us: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
