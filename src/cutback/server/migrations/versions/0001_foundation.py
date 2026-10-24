"""Immutable foundation schema; no application model imports."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("organization_id", sa.Uuid, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "project_id", sa.Uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.UniqueConstraint("project_id", "name"),
    )
    op.create_index("ix_environments_project_id", "environments", ["project_id"])
    op.create_table(
        "access_keys",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "project_id", sa.Uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("environment_id", sa.Uuid, sa.ForeignKey("environments.id", ondelete="CASCADE")),
        sa.Column("digest", sa.String(64), unique=True, nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_access_keys_project_id", "access_keys", ["project_id"])
    op.create_table(
        "llm_events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("event_id", sa.Uuid, nullable=False),
        sa.Column(
            "project_id", sa.Uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "environment_id",
            sa.Uuid,
            sa.ForeignKey("environments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workflow_id", sa.String(200)),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model_actual", sa.String(200)),
        sa.Column("cost_usd", sa.Numeric(24, 12)),
        sa.Column("cost_status", sa.String(60), nullable=False),
        sa.Column("cost_reason", sa.String(100)),
        sa.Column("price_snapshot_id", sa.String(100), nullable=False),
        sa.Column("cost_breakdown", sa.JSON, nullable=False),
        sa.Column("event", sa.JSON, nullable=False),
        sa.UniqueConstraint("project_id", "event_id"),
    )
    op.create_index("ix_events_project_time", "llm_events", ["project_id", "occurred_at"])
    op.create_index(
        "ix_events_project_workflow_time",
        "llm_events",
        ["project_id", "workflow_id", "occurred_at"],
    )
    op.create_index(
        "ix_events_project_model_time",
        "llm_events",
        ["project_id", "provider", "model_actual", "occurred_at"],
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("project_id", sa.Uuid, sa.ForeignKey("projects.id", ondelete="SET NULL")),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
    )


def downgrade():
    for table in (
        "audit_events",
        "llm_events",
        "access_keys",
        "environments",
        "projects",
        "organizations",
    ):
        op.drop_table(table)
