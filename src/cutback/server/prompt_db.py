"""Prompt identities, pinned versions, scoped read grants and immutable alias receipts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


def now():
    return datetime.now(UTC)


class PromptRecord(Base):
    __tablename__ = "prompt_registry"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_prompt_registry_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(100))
    workflow: Mapped[str] = mapped_column(String(200))
    creator: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "ordinal", name="uq_prompt_version_ordinal"),
        Index("ix_prompt_version_project", "project_id", "prompt_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_registry.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    document_hash: Mapped[str] = mapped_column(String(64))
    creator: Mapped[str] = mapped_column(String(100))
    provenance_ref: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptDependency(Base):
    __tablename__ = "prompt_dependencies"
    __table_args__ = (
        UniqueConstraint("version_id", "alias", name="uq_prompt_dependency_alias"),
        Index("ix_prompt_dependency_consumer", "project_id", "dependency_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(64))
    dependency_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id"))


class PromptAlias(Base):
    __tablename__ = "prompt_aliases"
    __table_args__ = (
        UniqueConstraint("prompt_id", "environment_id", "name", name="uq_prompt_alias"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_registry.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptAliasChange(Base):
    __tablename__ = "prompt_alias_changes"
    __table_args__ = (
        UniqueConstraint("alias_id", "revision", name="uq_prompt_alias_change_revision"),
        Index("ix_prompt_alias_change_project", "project_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    alias_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_aliases.id", ondelete="CASCADE"))
    from_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("prompt_versions.id"))
    to_version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id"))
    revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    approval_ref: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    quality_status: Mapped[str] = mapped_column(String(40))
    evidence_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("experiment_runs.id"))
    evidence_model_key: Mapped[str | None] = mapped_column(String(200))
    rollback_of: Mapped[UUID | None] = mapped_column(ForeignKey("prompt_alias_changes.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptReadGrant(Base):
    __tablename__ = "prompt_read_grants"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "actor",
            "prompt_id",
            "environment_id",
            "workflow",
            "policy_id",
            name="uq_prompt_read_grant",
        ),
        Index("ix_prompt_read_grant_actor", "project_id", "actor"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(64))
    prompt_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_registry.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    workflow: Mapped[str] = mapped_column(String(200))
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("capture_policies.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    approved_by: Mapped[str] = mapped_column(String(100))
    approval_ref: Mapped[str] = mapped_column(String(200))


class PromptObservation(Base):
    __tablename__ = "prompt_observations"
    __table_args__ = (
        UniqueConstraint("project_id", "observation_id", name="uq_prompt_observation"),
        Index(
            "ix_prompt_observation_version",
            "project_id",
            "environment_id",
            "version_id",
            "received_at",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id"))
    observation_id: Mapped[UUID] = mapped_column(Uuid)
    fingerprint: Mapped[str] = mapped_column(String(64))
    metadata_record: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PromptExperimentUse(Base):
    __tablename__ = "prompt_experiment_uses"
    __table_args__ = (
        UniqueConstraint("plan_id", "version_id", "model_key", name="uq_prompt_experiment_use"),
        Index("ix_prompt_experiment_consumer", "project_id", "version_id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_plans.id", ondelete="CASCADE"))
    version_id: Mapped[UUID] = mapped_column(ForeignKey("prompt_versions.id"))
    model_key: Mapped[str] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(100))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
