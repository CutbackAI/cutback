import hashlib
import hmac
import importlib
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_projects_organization_slug"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"))
    slug: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        UniqueConstraint("id", "project_id", name="uq_environment_project_identity"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))


class AccessKey(Base):
    __tablename__ = "access_keys"
    __table_args__ = (
        UniqueConstraint(
            "id", "project_id", "environment_id", name="uq_access_key_project_environment"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE")
    )
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoredEvent(Base):
    __tablename__ = "llm_events"
    __table_args__ = (
        UniqueConstraint("project_id", "event_id"),
        Index("ix_events_project_time", "project_id", "occurred_at"),
        Index("ix_events_project_workflow_time", "project_id", "workflow_id", "occurred_at"),
        Index(
            "ix_events_project_model_time", "project_id", "provider", "model_actual", "occurred_at"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(Uuid)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    workflow_id: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    model_actual: Mapped[str | None] = mapped_column(String(200))
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    cost_status: Mapped[str] = mapped_column(String(60))
    cost_reason: Mapped[str | None] = mapped_column(String(100))
    price_snapshot_id: Mapped[str] = mapped_column(String(100))
    cost_breakdown: Mapped[dict] = mapped_column(JSON)
    event: Mapped[dict] = mapped_column(JSON)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    details: Mapped[dict] = mapped_column(JSON)


class ScanRun(Base):
    __tablename__ = "scan_runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    inventory: Mapped[dict] = mapped_column(JSON)


class WorkflowRecord(Base):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("project_id", "stable_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    stable_key: Mapped[str] = mapped_column(String(200))
    entry_point: Mapped[dict] = mapped_column(JSON)
    attribution_method: Mapped[str] = mapped_column(String(30))


class DailyUsage(Base):
    __tablename__ = "usage_daily"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    environment: Mapped[str] = mapped_column(String(200))
    workflow: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    traffic_kind: Mapped[str] = mapped_column(
        String(30), default="unknown", server_default="unknown"
    )
    call_site: Mapped[str] = mapped_column(
        String(200), default="unattributed", server_default="unattributed"
    )
    report: Mapped[dict] = mapped_column(JSON)


class SDKHealth(Base):
    __tablename__ = "collector_health"
    __table_args__ = (UniqueConstraint("project_id", "instance_id"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    instance_id: Mapped[UUID] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict] = mapped_column(JSON)


class RateBucket(Base):
    __tablename__ = "rate_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window: Mapped[int] = mapped_column(BigInteger)
    count: Mapped[int] = mapped_column(Integer)


class RateOverride(Base):
    __tablename__ = "rate_overrides"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(200))
    region: Mapped[str | None] = mapped_column(String(200))
    service_tier: Mapped[str | None] = mapped_column(String(200))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rates: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[str] = mapped_column(String(100))


Index(
    "ix_rate_override_effective_lookup",
    RateOverride.project_id,
    RateOverride.provider,
    RateOverride.model,
    RateOverride.valid_from.desc(),
)


class CapturePolicyRecord(Base):
    __tablename__ = "capture_policies"
    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_capture_policy_scope"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_by: Mapped[str] = mapped_column(String(100))
    policy: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PayloadSample(Base):
    __tablename__ = "payload_samples"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_payload_project_scope"),
        Index("ix_payload_native_profile_scan", "project_id", "policy_id", "id"),
        CheckConstraint(
            "(native_profile_ref IS NULL AND native_profile_sha256 IS NULL) OR "
            "(native_profile_ref IS NOT NULL AND native_profile_sha256 IS NOT NULL)",
            name="ck_payload_native_profile_pair",
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("capture_policies.id"))
    object_ref: Mapped[str] = mapped_column(String(500))
    native_profile_ref: Mapped[str | None] = mapped_column(String(500))
    native_profile_sha256: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    metadata_safe: Mapped[dict] = mapped_column(JSON)


class DatasetRecord(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_dataset_project_scope"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_id: Mapped[str] = mapped_column(String(200))
    immutable_hash: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DatasetItem(Base):
    __tablename__ = "dataset_items"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id"))
    split: Mapped[str] = mapped_column(String(20))
    group_hash: Mapped[str] = mapped_column(String(64))
    lineage_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dataset_families.id", name="fk_dataset_item_lineage", ondelete="SET NULL")
    )


class ExperimentPlanRecord(Base):
    __tablename__ = "experiment_plans"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_experiment_plan_project_scope"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    dataset_id: Mapped[UUID] = mapped_column(ForeignKey("datasets.id"))
    specification: Mapped[dict] = mapped_column(JSON)
    approval_status: Mapped[str] = mapped_column(String(30), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(100))


class ExperimentRunRecord(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_experiment_run_project_identity"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_plans.id"))
    status: Mapped[str] = mapped_column(String(30))
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    call_ledger_version: Mapped[int | None] = mapped_column(Integer)
    known_cost_requests: Mapped[int | None] = mapped_column(Integer)
    results: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class JobRecord(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    execution_kind: Mapped[str] = mapped_column(
        String(30), default="evaluation", server_default="evaluation"
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), server_default=func.now()
    )
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("id", "project_id", "run_id", name="uq_job_project_run_scope"),
        Index("ix_jobs_eligibility", "status", "available_at", "created_at"),
        Index(
            "ix_jobs_kind_eligibility",
            "execution_kind",
            "status",
            "available_at",
            "created_at",
            "id",
        ),
    )


class RecommendationRecord(Base):
    __tablename__ = "recommendations"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[UUID] = mapped_column(ForeignKey("experiment_runs.id"))
    status: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[dict] = mapped_column(JSON)


class ChangeProposal(Base):
    __tablename__ = "change_proposals"
    __table_args__ = (UniqueConstraint("id", "project_id", name="uq_change_project_identity"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    recommendation_id: Mapped[UUID] = mapped_column(ForeignKey("recommendations.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    evidence: Mapped[dict] = mapped_column(JSON)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, digest: str) -> bool:
    return hmac.compare_digest(token_hash(token), digest)


def new_token() -> str:
    return secrets.token_urlsafe(48)


def database(url: str):
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("collector requires postgresql+psycopg; SQLite is not a telemetry backend")
    engine = create_engine(
        url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000 -c timezone=UTC"},
    )
    return engine, sessionmaker(engine, expire_on_commit=False)


# Register the additive shared control-plane models only after Base and foundation classes exist.
importlib.import_module("cutback.server.enterprise_db")
importlib.import_module("cutback.server.trace_db")
importlib.import_module("cutback.server.lineage_db")
importlib.import_module("cutback.server.review_db")
importlib.import_module("cutback.server.prompt_db")
importlib.import_module("cutback.server.monitoring_db")
importlib.import_module("cutback.server.quality_db")
importlib.import_module("cutback.server.execution_db")
importlib.import_module("cutback.server.economics_db")
importlib.import_module("cutback.server.campaign_db")
importlib.import_module("cutback.server.outcome_watch_db")
importlib.import_module("cutback.server.gateway_db")
importlib.import_module("cutback.server.provider_batch_db")
importlib.import_module("cutback.server.semantic_cache_db")
importlib.import_module("cutback.server.semantic_upload_db")
importlib.import_module("cutback.server.tool_db")
importlib.import_module("cutback.server.guardrail_db")
importlib.import_module("cutback.server.criteria_db")
importlib.import_module("cutback.server.scenario_db")
importlib.import_module("cutback.server.native_profile_db")
importlib.import_module("cutback.server.routing_program_db")
importlib.import_module("cutback.server.policy_program_db")
importlib.import_module("cutback.server.policy_activity_db")
importlib.import_module("cutback.server.comment_db")
