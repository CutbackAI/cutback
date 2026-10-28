"""Scoped adaptive campaign state; prompt content stays in encrypted payloads."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base


class CampaignRecord(Base):
    __tablename__ = "improvement_campaigns"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "environment_id", name="uq_campaign_scope"),
        UniqueConstraint("project_id", "request_id", name="uq_campaign_request"),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_campaign_environment_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            name="fk_campaign_dataset_scope",
        ),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_campaign_policy_scope",
        ),
        ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_campaign_payload_scope",
        ),
        CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0", name="ck_campaign_money"),
        CheckConstraint(
            "requests_used >= 0 AND requests_used <= 10000 "
            "AND known_cost_requests >= 0 AND known_cost_requests <= requests_used",
            name="ck_campaign_request_counts",
        ),
        CheckConstraint("current_round >= 1 AND current_round <= 3", name="ck_campaign_round"),
        Index("ix_campaign_eligibility", "status", "available_at", "created_at", "id"),
        Index("ix_campaign_project_time", "project_id", "environment_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    dataset_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    payload_id: Mapped[UUID] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    approval_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    economic_envelope: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="READY_FOR_APPROVAL")
    approval_status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(100))
    current_round: Mapped[int] = mapped_column(Integer, default=1)
    reserved_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    spent_usd: Mapped[Decimal] = mapped_column(Numeric(24, 12), default=Decimal(0))
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    known_cost_requests: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CampaignRun(Base):
    __tablename__ = "campaign_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_campaign_run_owner"),
        UniqueConstraint(
            "run_id", "campaign_id", "project_id", "environment_id", name="uq_campaign_run_scope"
        ),
        UniqueConstraint("campaign_id", "step_key", name="uq_campaign_step"),
        ForeignKeyConstraint(
            ["campaign_id", "project_id", "environment_id"],
            [
                "improvement_campaigns.id",
                "improvement_campaigns.project_id",
                "improvement_campaigns.environment_id",
            ],
            name="fk_campaign_run_parent_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_campaign_run_experiment_scope",
        ),
        CheckConstraint(
            "phase IN ('proposal','validation','confirmation')", name="ck_campaign_run_phase"
        ),
        CheckConstraint("round_index >= 1 AND round_index <= 3", name="ck_campaign_run_round"),
        Index("ix_campaign_run_project", "project_id", "campaign_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    campaign_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    phase: Mapped[str] = mapped_column(String(20))
    step_key: Mapped[str] = mapped_column(String(100))
    round_index: Mapped[int] = mapped_column(Integer)
    campaign_sha256: Mapped[str] = mapped_column(String(64))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    derivation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CampaignCandidate(Base):
    __tablename__ = "campaign_candidates"
    __table_args__ = (
        UniqueConstraint(
            "id", "campaign_id", "project_id", "environment_id", name="uq_campaign_candidate_scope"
        ),
        UniqueConstraint("campaign_id", "model_key", name="uq_campaign_candidate_key"),
        UniqueConstraint(
            "campaign_id", "round_index", "ordinal", name="uq_campaign_candidate_ordinal"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "project_id", "environment_id"],
            [
                "improvement_campaigns.id",
                "improvement_campaigns.project_id",
                "improvement_campaigns.environment_id",
            ],
            name="fk_campaign_candidate_parent_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["proposal_run_id", "campaign_id", "project_id", "environment_id"],
            [
                "campaign_runs.run_id",
                "campaign_runs.campaign_id",
                "campaign_runs.project_id",
                "campaign_runs.environment_id",
            ],
            name="fk_campaign_candidate_proposal_scope",
        ),
        ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_campaign_candidate_payload_scope",
        ),
        CheckConstraint("kind IN ('seed','generated')", name="ck_campaign_candidate_kind"),
        CheckConstraint(
            "round_index >= 0 AND round_index <= 3 AND ordinal >= 1 AND ordinal <= 5",
            name="ck_campaign_candidate_order",
        ),
        Index("ix_campaign_candidate_project", "project_id", "campaign_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    campaign_id: Mapped[UUID] = mapped_column(Uuid)
    proposal_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    payload_id: Mapped[UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(20))
    model_key: Mapped[str] = mapped_column(String(200))
    round_index: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    configuration_sha256: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CampaignSelection(Base):
    __tablename__ = "campaign_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "project_id", "environment_id"],
            [
                "improvement_campaigns.id",
                "improvement_campaigns.project_id",
                "improvement_campaigns.environment_id",
            ],
            name="fk_campaign_selection_parent_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "campaign_id", "project_id", "environment_id"],
            [
                "campaign_candidates.id",
                "campaign_candidates.campaign_id",
                "campaign_candidates.project_id",
                "campaign_candidates.environment_id",
            ],
            name="fk_campaign_selection_candidate_scope",
        ),
        ForeignKeyConstraint(
            ["validation_run_id", "campaign_id", "project_id", "environment_id"],
            [
                "campaign_runs.run_id",
                "campaign_runs.campaign_id",
                "campaign_runs.project_id",
                "campaign_runs.environment_id",
            ],
            name="fk_campaign_selection_validation_scope",
        ),
        Index("ix_campaign_selection_project", "project_id", "campaign_id"),
    )
    campaign_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    candidate_id: Mapped[UUID] = mapped_column(Uuid)
    validation_run_id: Mapped[UUID] = mapped_column(Uuid)
    selection_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CampaignEconomicReference(Base):
    """Raw snapshot expiry can remove this link without erasing cumulative campaign costs."""

    __tablename__ = "campaign_economic_references"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "project_id", "environment_id"],
            [
                "improvement_campaigns.id",
                "improvement_campaigns.project_id",
                "improvement_campaigns.environment_id",
            ],
            name="fk_campaign_economics_parent_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_campaign_economics_snapshot_scope",
            ondelete="CASCADE",
        ),
        Index("ix_campaign_economics_project", "project_id", "campaign_id"),
    )
    campaign_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
