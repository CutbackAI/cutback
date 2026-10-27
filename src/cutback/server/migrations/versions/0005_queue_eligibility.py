"""Durable queue age and bounded blocked-work backoff; no billable retries."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("jobs", sa.Column("available_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE jobs SET created_at = experiment_runs.created_at FROM experiment_runs "
        "WHERE jobs.run_id = experiment_runs.id"
    )
    op.create_index("ix_jobs_eligibility", "jobs", ["status", "available_at", "created_at"])


def downgrade():
    op.drop_index("ix_jobs_eligibility", table_name="jobs")
    op.drop_column("jobs", "available_at")
    op.drop_column("jobs", "created_at")
