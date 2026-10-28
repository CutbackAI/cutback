"""Durable metadata-only online rules, work receipts and incident state."""

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


class OnlineRule(Base):
    __tablename__ = "online_rules"
    __table_args__ = (
        UniqueConstraint("project_id", "key", "revision", name="uq_online_rule_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    creator: Mapped[str] = mapped_column(String(100))
    evaluations_used: Mapped[int] = mapped_column(Integer, default=0)
    backfill_cursor: Mapped[UUID | None] = mapped_column(Uuid)
    backfill_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    last_served_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OnlineEvaluation(Base):
    __tablename__ = "online_evaluations"
    __table_args__ = (
        UniqueConstraint("rule_id", "span_record_id", name="uq_online_evaluation_target"),
        Index("ix_online_evaluation_queue", "rule_id", "state", "available_at"),
        Index("ix_online_evaluation_window", "project_id", "rule_id", "occurred_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("online_rules.id", ondelete="CASCADE"))
    # SET NULL preserves the missing/retained result denominator after raw trace expiry.
    span_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("execution_spans.id", ondelete="SET NULL")
    )
    trace_id: Mapped[str] = mapped_column(String(32))
    span_id: Mapped[str] = mapped_column(String(16))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class OnlineIntake(Base):
    __tablename__ = "online_intake"
    __table_args__ = (
        UniqueConstraint("span_record_id", name="uq_online_intake_span"),
        Index("ix_online_intake_project", "project_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    span_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_spans.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (
        UniqueConstraint("project_id", "key", "revision", name="uq_alert_rule_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    online_rule_id: Mapped[UUID] = mapped_column(ForeignKey("online_rules.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_window: Mapped[dict] = mapped_column(JSON, default=dict)
    window_queue_full: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    backfill_cursor: Mapped[UUID | None] = mapped_column(Uuid)
    backfill_complete: Mapped[bool] = mapped_column(Boolean, default=False)


class AlertWindow(Base):
    __tablename__ = "alert_windows"
    __table_args__ = (
        UniqueConstraint("alert_rule_id", "window_start", name="uq_alert_window"),
        Index("ix_alert_window_dirty", "alert_rule_id", "dirty", "window_end"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    alert_rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("alert_rule_id", "window_start", name="uq_incident_activation"),
        Index("ix_incident_page", "project_id", "created_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    alert_rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id", ondelete="CASCADE"))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="open")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    owner: Mapped[str | None] = mapped_column(String(200))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        UniqueConstraint("incident_id", "revision", name="uq_incident_event_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(30))
    owner: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SavedView(Base):
    __tablename__ = "saved_views"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_saved_view_key"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    specification: Mapped[dict] = mapped_column(JSON)


class NotificationConfig(Base):
    __tablename__ = "notification_configs"
    __table_args__ = (
        UniqueConstraint("project_id", "destination", name="uq_notification_destination"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    destination: Mapped[str] = mapped_column(String(100))
    destination_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    approval_ref: Mapped[str] = mapped_column(String(200))


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("config_id", "event_id", name="uq_notification_event"),
        Index("ix_notification_delivery_queue", "state", "available_at"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_configs.id", ondelete="CASCADE")
    )
    config_revision: Mapped[int] = mapped_column(Integer)
    destination_hash: Mapped[str] = mapped_column(String(64))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("incident_events.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    code: Mapped[str | None] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON)
