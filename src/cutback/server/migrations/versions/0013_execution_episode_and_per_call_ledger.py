"""Scoped execution episodes and durable native call receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    # Composite parent identities must exist before scoped child foreign keys.
    op.create_unique_constraint(
        "uq_environment_project_identity", "environments", ["id", "project_id"]
    )
    op.add_column("experiment_runs", sa.Column("call_ledger_version", sa.Integer(), nullable=True))
    op.add_column("experiment_runs", sa.Column("known_cost_requests", sa.Integer(), nullable=True))
    op.create_unique_constraint(
        "uq_experiment_run_project_identity", "experiment_runs", ["id", "project_id"]
    )
    op.create_table(
        "execution_episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column("case_key", sa.String(length=64), nullable=False),
        sa.Column("repeat_index", sa.Integer(), nullable=False),
        sa.Column("execution_version", sa.String(length=50), nullable=False),
        sa.Column("max_requests", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=True),
        sa.Column("requests_used", sa.Integer(), nullable=False),
        sa.Column("known_cost_requests", sa.Integer(), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("spent_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("repeat_index >= 0 AND repeat_index < 5", name="ck_episode_repeat"),
        sa.CheckConstraint(
            "max_requests >= 1 AND max_requests <= 32 AND requests_used <= max_requests",
            name="ck_episode_turn_budget",
        ),
        sa.CheckConstraint(
            "requests_used >= 0 AND known_cost_requests >= 0 "
            "AND known_cost_requests <= requests_used",
            name="ck_episode_request_counts",
        ),
        sa.CheckConstraint("reserved_usd >= 0 AND spent_usd >= 0", name="ck_episode_money"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_episode_environment_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_episode_run_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "environment_id", "run_id", "project_id", name="uq_execution_episode_scope"
        ),
        sa.UniqueConstraint(
            "run_id", "model_key", "case_key", "repeat_index", name="uq_execution_episode_case"
        ),
    )
    op.create_index(
        "ix_execution_episode_run",
        "execution_episodes",
        ["project_id", "run_id", "id"],
        unique=False,
    )
    op.create_table(
        "execution_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("episode_id", sa.Uuid(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("model_key", sa.String(length=200), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("price_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("spent_usd", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0) "
            "AND (output_tokens IS NULL OR output_tokens >= 0)",
            name="ck_execution_call_tokens",
        ),
        sa.CheckConstraint(
            "reserved_usd >= 0 AND (spent_usd IS NULL OR spent_usd >= 0)",
            name="ck_execution_call_money",
        ),
        sa.CheckConstraint("sequence > 0 AND sequence <= 10000", name="ck_execution_call_sequence"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_execution_call_environment_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "environment_id", "run_id", "project_id"],
            [
                "execution_episodes.id",
                "execution_episodes.environment_id",
                "execution_episodes.run_id",
                "execution_episodes.project_id",
            ],
            name="fk_execution_call_episode_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_execution_call_run_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_execution_call_sequence"),
    )
    op.create_index(
        "ix_execution_call_lease",
        "execution_calls",
        ["project_id", "state", "lease_until"],
        unique=False,
    )
    op.create_index(
        "ix_execution_call_run",
        "execution_calls",
        ["project_id", "run_id", "sequence"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM execution_calls) OR "
            "EXISTS (SELECT 1 FROM execution_episodes) OR "
            "EXISTS (SELECT 1 FROM experiment_runs WHERE call_ledger_version IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_NATIVE_LEDGER_DATA_BEFORE_DOWNGRADE")
    # Never silently discard retained accounting or change legacy run coverage.
    op.drop_index("ix_execution_call_run", table_name="execution_calls")
    op.drop_index("ix_execution_call_lease", table_name="execution_calls")
    op.drop_table("execution_calls")
    op.drop_index("ix_execution_episode_run", table_name="execution_episodes")
    op.drop_table("execution_episodes")
    op.drop_constraint("uq_experiment_run_project_identity", "experiment_runs", type_="unique")
    op.drop_column("experiment_runs", "known_cost_requests")
    op.drop_column("experiment_runs", "call_ledger_version")
    op.drop_constraint("uq_environment_project_identity", "environments", type_="unique")
