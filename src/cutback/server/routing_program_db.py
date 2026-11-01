"""Scoped routing programs; examples, instructions and fitted evidence stay encrypted."""

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


class RoutingProgramRecord(Base):
    __tablename__ = "routing_improvement_programs"
    __table_args__ = (
        UniqueConstraint("id", "project_id", "environment_id", name="uq_routing_program_scope"),
        UniqueConstraint("project_id", "request_id", name="uq_routing_program_request"),
        ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_routing_program_environment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            name="fk_routing_program_dataset",
        ),
        ForeignKeyConstraint(
            ["template_plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            name="fk_routing_program_template",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_routing_program_snapshot",
        ),
        ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_routing_program_policy",
        ),
        ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_routing_program_payload",
        ),
        ForeignKeyConstraint(
            ["fit_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_routing_program_fit",
        ),
        CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0", name="ck_routing_program_money"),
        CheckConstraint(
            "requests_used >= 0 AND requests_used <= 10000 "
            "AND known_cost_requests >= 0 AND known_cost_requests <= requests_used",
            name="ck_routing_program_requests",
        ),
        CheckConstraint(
            "status IN ('READY_FOR_APPROVAL','QUEUED','WAITING_STATIC','WAITING_TRAINING',"
            "'WAITING_ROUTED','WAITING_CONFIRMATION','COMPLETED','FAILED','CANCELLED','EXPIRED',"
            "'ABSTAINED')",
            name="ck_routing_program_status",
        ),
        CheckConstraint(
            "approval_status IN ('pending','consumed')", name="ck_routing_program_approval"
        ),
        Index("ix_routing_program_queue", "status", "available_at", "created_at", "id"),
        Index("ix_routing_program_project", "project_id", "environment_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    request_id: Mapped[UUID] = mapped_column(Uuid)
    dataset_id: Mapped[UUID] = mapped_column(Uuid)
    template_plan_id: Mapped[UUID] = mapped_column(Uuid)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid)
    policy_id: Mapped[UUID] = mapped_column(Uuid)
    payload_id: Mapped[UUID] = mapped_column(Uuid)
    fit_id: Mapped[UUID | None] = mapped_column(Uuid)
    request_sha256: Mapped[str] = mapped_column(String(64))
    template_plan_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    approval_sha256: Mapped[str] = mapped_column(String(64))
    specification: Mapped[dict] = mapped_column(JSON)
    economic_envelope: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(40), default="READY_FOR_APPROVAL")
    approval_status: Mapped[str] = mapped_column(String(20), default="pending")
    approved_by: Mapped[str | None] = mapped_column(String(100))
    reserved_usd: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), default=Decimal(0), server_default="0"
    )
    spent_usd: Mapped[Decimal] = mapped_column(
        Numeric(24, 12), default=Decimal(0), server_default="0"
    )
    requests_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    known_cost_requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
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


class RoutingProgramRun(Base):
    __tablename__ = "routing_program_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_routing_program_run_owner"),
        UniqueConstraint("program_id", "phase", name="uq_routing_program_phase"),
        UniqueConstraint(
            "run_id",
            "program_id",
            "project_id",
            "environment_id",
            name="uq_routing_program_run_scope",
        ),
        ForeignKeyConstraint(
            ["program_id", "project_id", "environment_id"],
            [
                "routing_improvement_programs.id",
                "routing_improvement_programs.project_id",
                "routing_improvement_programs.environment_id",
            ],
            name="fk_routing_program_run_parent",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_routing_program_run_experiment",
        ),
        CheckConstraint(
            "phase IN ('static_validation','training','routed_validation','confirmation')",
            name="ck_routing_program_phase",
        ),
        Index("ix_routing_program_run_project", "project_id", "program_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    program_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(Uuid)
    phase: Mapped[str] = mapped_column(String(24))
    program_sha256: Mapped[str] = mapped_column(String(64))
    plan_sha256: Mapped[str] = mapped_column(String(64))
    derivation: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RoutingProgramSelection(Base):
    __tablename__ = "routing_program_selections"
    __table_args__ = (
        UniqueConstraint("program_id", name="uq_routing_program_selection"),
        ForeignKeyConstraint(
            ["program_id", "project_id", "environment_id"],
            [
                "routing_improvement_programs.id",
                "routing_improvement_programs.project_id",
                "routing_improvement_programs.environment_id",
            ],
            name="fk_routing_program_selection_parent",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["validation_run_id", "program_id", "project_id", "environment_id"],
            [
                "routing_program_runs.run_id",
                "routing_program_runs.program_id",
                "routing_program_runs.project_id",
                "routing_program_runs.environment_id",
            ],
            name="fk_routing_program_selection_validation",
        ),
        CheckConstraint(
            "strategy IN ('static_native','learned_input_size')", name="ck_routing_program_strategy"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(Uuid)
    program_id: Mapped[UUID] = mapped_column(Uuid)
    validation_run_id: Mapped[UUID] = mapped_column(Uuid)
    candidate_key: Mapped[str] = mapped_column(String(100))
    strategy: Mapped[str] = mapped_column(String(24))
    configuration_sha256: Mapped[str] = mapped_column(String(64))
    program_sha256: Mapped[str] = mapped_column(String(64))
    selection_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
