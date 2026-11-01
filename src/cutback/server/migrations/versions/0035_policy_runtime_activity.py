"""Scoped content-free policy activity; preserve released migrations."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_policy_activity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("workflow", sa.String(200), nullable=False),
        sa.Column("service_name", sa.String(200), nullable=False),
        sa.Column("source_commit", sa.String(64), nullable=False),
        sa.Column("call_site_id", sa.String(200), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("started_ns", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("metadata_record", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_policy_activity_environment",
        ),
        sa.UniqueConstraint(
            "project_id", "environment_id", "event_id", name="uq_policy_activity_event"
        ),
    )
    op.create_index(
        "ix_policy_activity_scope",
        "source_policy_activity",
        [
            "project_id",
            "environment_id",
            "workflow",
            "service_name",
            "source_commit",
            "call_site_id",
            "policy_sha256",
            "started_ns",
            "event_id",
        ],
    )
    op.create_index(
        "ix_policy_activity_retention", "source_policy_activity", ["project_id", "received_at"]
    )


def downgrade():
    # Downgrading must never silently discard evidence used to inspect current behavior.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM source_policy_activity)")):
        raise ValueError("POLICY_ACTIVITY_EXPORT_AND_REMOVE_BEFORE_DOWNGRADE")
    op.drop_table("source_policy_activity")
