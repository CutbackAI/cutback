"""Content-free execution evidence, independent from billable native LLM attempts."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from cutback.server.db import Base, StoredEvent


class ExecutionTrace(Base):
    __tablename__ = "execution_traces"
    __table_args__ = (
        UniqueConstraint("project_id", "environment_id", "trace_id", name="uq_execution_trace"),
        Index("ix_execution_trace_page", "project_id", "environment_id", "received_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    trace_id: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ExecutionSpan(Base):
    __tablename__ = "execution_spans"
    __table_args__ = (
        UniqueConstraint("trace_record_id", "span_id", name="uq_execution_span"),
        Index("ix_execution_span_time", "project_id", "started_ns"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    trace_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="CASCADE")
    )
    span_id: Mapped[str] = mapped_column(String(16))
    started_ns: Mapped[int] = mapped_column(BigInteger)
    fingerprint: Mapped[str] = mapped_column(String(64))
    metadata_record: Mapped[dict] = mapped_column(JSON)


class ExecutionSession(Base):
    __tablename__ = "execution_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "environment_id", "session_id", name="uq_execution_session"),
        Index("ix_execution_session_page", "project_id", "environment_id", "received_at", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class SessionTrace(Base):
    __tablename__ = "execution_session_traces"
    __table_args__ = (
        UniqueConstraint("session_record_id", "trace_record_id", name="uq_session_trace"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    session_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE")
    )
    trace_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="CASCADE")
    )


class ExternalEvaluation(Base):
    __tablename__ = "external_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "environment_id", "result_id", name="uq_external_evaluation"
        ),
        Index("ix_external_evaluation_target", "project_id", "environment_id", "trace_id", "id"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    result_id: Mapped[UUID] = mapped_column(Uuid)
    trace_id: Mapped[str | None] = mapped_column(String(32))
    session_id: Mapped[str | None] = mapped_column(String(200))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    metadata_record: Mapped[dict] = mapped_column(JSON)


class TraceContent(Base):
    __tablename__ = "execution_trace_content"
    __table_args__ = (UniqueConstraint("trace_record_id", "span_id", name="uq_trace_content"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    environment_id: Mapped[UUID] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"))
    trace_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_traces.id", ondelete="CASCADE")
    )
    span_id: Mapped[str] = mapped_column(String(16))
    payload_id: Mapped[UUID] = mapped_column(ForeignKey("payload_samples.id", ondelete="CASCADE"))


# Exact identity lookup also works for already collected native attempts.
Index(
    "ix_llm_trace_identity",
    StoredEvent.project_id,
    StoredEvent.environment_id,
    StoredEvent.event["trace_id"].as_string(),
    StoredEvent.event["span_id"].as_string(),
)
