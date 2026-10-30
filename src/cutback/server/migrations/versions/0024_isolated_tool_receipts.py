"""One-shot isolated tool intents scoped to canonical execution episodes."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("broker_id", sa.Uuid(), nullable=False),
        sa.Column("environment_name", sa.String(200), nullable=False),
        sa.Column("workflow", sa.String(200), nullable=False),
        sa.Column("broker_generation", sa.Uuid(), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("image_id", sa.String(71), nullable=False),
        sa.Column("max_calls", sa.Integer(), nullable=False),
        sa.Column("max_wall_seconds", sa.Integer(), nullable=False),
        sa.Column("calls_used", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("cleanup_confirmed", sa.Boolean(), nullable=False),
        sa.Column("cleanup_requested", sa.Boolean(), nullable=False),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False),
        sa.Column("cleanup_lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "id", "environment_id", "run_id", "project_id", name="uq_tool_session_scope"
        ),
        sa.UniqueConstraint("episode_id", name="uq_tool_session_episode"),
        sa.ForeignKeyConstraint(
            ["episode_id", "environment_id", "run_id", "project_id"],
            [
                "execution_episodes.id",
                "execution_episodes.environment_id",
                "execution_episodes.run_id",
                "execution_episodes.project_id",
            ],
            ondelete="CASCADE",
            name="fk_tool_session_episode_scope",
        ),
        sa.CheckConstraint(
            "max_calls >= 1 AND max_calls <= 100 AND calls_used >= 0 AND calls_used <= max_calls",
            name="ck_tool_session_call_bound",
        ),
        sa.CheckConstraint(
            "max_wall_seconds >= 1 AND max_wall_seconds <= 120", name="ck_tool_session_wall_bound"
        ),
        sa.CheckConstraint(
            "state IN ('STARTING','READY','UNCERTAIN','CLOSED')", name="ck_tool_session_state"
        ),
        sa.CheckConstraint(
            "cleanup_attempts >= 0 AND cleanup_attempts <= 3", name="ck_tool_cleanup_attempts"
        ),
    )
    op.create_index("ix_tool_session_project_run", "tool_sessions", ["project_id", "run_id", "id"])
    op.create_index(
        "ix_tool_session_cleanup",
        "tool_sessions",
        ["project_id", "cleanup_confirmed", "lease_until"],
    )
    op.create_table(
        "tool_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tool_kind", sa.String(20), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("state_changed", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["session_id", "environment_id", "run_id", "project_id"],
            [
                "tool_sessions.id",
                "tool_sessions.environment_id",
                "tool_sessions.run_id",
                "tool_sessions.project_id",
            ],
            ondelete="CASCADE",
            name="fk_tool_operation_session_scope",
        ),
        sa.UniqueConstraint("session_id", "sequence", name="uq_tool_operation_sequence"),
        sa.CheckConstraint("sequence >= 1 AND sequence <= 100", name="ck_tool_operation_sequence"),
        sa.CheckConstraint(
            "state IN ('DISPATCHING','RETURNED','DENIED','UNCERTAIN')",
            name="ck_tool_operation_state",
        ),
        sa.CheckConstraint(
            "tool_kind IN ('records_read','records_patch','unapproved')",
            name="ck_tool_operation_kind",
        ),
    )
    op.create_index(
        "ix_tool_operation_project_run", "tool_operations", ["project_id", "run_id", "id"]
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT EXISTS (SELECT 1 FROM tool_sessions)")).scalar():
        raise RuntimeError("ISOLATED_TOOL_DOWNGRADE_REQUIRES_EXPLICIT_METADATA_EXPORT_AND_DELETE")
    op.drop_table("tool_operations")
    op.drop_table("tool_sessions")
