"""Durable asynchronous native batch approval, intent and reconciliation metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_experiment_plan_project_scope", "experiment_plans", ["id", "project_id"]
    )
    op.create_unique_constraint("uq_job_project_run_scope", "jobs", ["id", "project_id", "run_id"])
    op.create_table(
        "provider_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("pieces", sa.JSON(), nullable=False),
        sa.Column("calls", sa.JSON(), nullable=False),
        sa.Column("active_elapsed_us", sa.BigInteger(), nullable=False),
        sa.Column("active_reserved_us", sa.BigInteger(), nullable=False),
        sa.Column("active_uncertain_us", sa.BigInteger(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_plan_scope",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "project_id", "run_id"],
            ["jobs.id", "jobs.project_id", "jobs.run_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_job_scope",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_run_scope",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_provider_batch_environment_scope",
        ),
        sa.UniqueConstraint("project_id", "plan_id", name="uq_provider_batch_plan"),
        sa.UniqueConstraint("id", "project_id", "run_id", name="uq_provider_batch_run_identity"),
        sa.CheckConstraint(
            "json_typeof(pieces) = 'array' AND json_array_length(pieces) <= 6",
            name="ck_provider_batch_piece_bound",
        ),
        sa.CheckConstraint(
            "json_typeof(calls) = 'array' AND json_array_length(calls) <= 1000",
            name="ck_provider_batch_call_bound",
        ),
        sa.CheckConstraint(
            "active_elapsed_us >= 0 AND active_reserved_us >= 0 "
            "AND active_reserved_us <= 180000000 AND active_uncertain_us >= 0",
            name="ck_provider_batch_active_wall",
        ),
    )
    op.create_index(
        "ix_provider_batch_eligibility", "provider_batches", ["state", "available_at", "created_at"]
    )
    op.add_column("execution_calls", sa.Column("provider_batch_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_execution_call_provider_batch_scope",
        "execution_calls",
        "provider_batches",
        ["provider_batch_id", "project_id", "run_id"],
        ["id", "project_id", "run_id"],
        ondelete="CASCADE",
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM provider_batches)")).scalar():
        raise RuntimeError("EXPLICITLY_RECONCILE_AND_DELETE_BATCH_DATA_BEFORE_DOWNGRADE")
    op.drop_constraint(
        "fk_execution_call_provider_batch_scope", "execution_calls", type_="foreignkey"
    )
    op.drop_column("execution_calls", "provider_batch_id")
    op.drop_table("provider_batches")
    op.drop_constraint("uq_job_project_run_scope", "jobs", type_="unique")
    op.drop_constraint("uq_experiment_plan_project_scope", "experiment_plans", type_="unique")
