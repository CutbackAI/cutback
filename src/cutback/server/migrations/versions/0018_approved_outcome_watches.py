"""Explicitly approved metadata-only later-window outcome policies."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_improvement_scope", "improvement_observations", ["id", "project_id", "environment_id"]
    )
    op.create_table(
        "outcome_watches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("watch_id", sa.Uuid(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("approval_ref", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_window_index", sa.Integer(), nullable=False),
        sa.Column("windows_processed", sa.Integer(), nullable=False),
        sa.Column("windows_skipped", sa.Integer(), nullable=False),
        sa.Column("last_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_outcome_watch_environment_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("project_id", "watch_id", name="uq_outcome_watch_request"),
        sa.UniqueConstraint("id", "project_id", "environment_id", name="uq_outcome_watch_scope"),
        sa.CheckConstraint(
            "windows_processed >= 0 AND windows_processed <= 100 "
            "AND last_window_index >= -1 AND last_window_index <= 1000000 "
            "AND windows_skipped >= 0 AND windows_skipped <= 1000000",
            name="ck_outcome_watch_window_bounds",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','APPROVED','PAUSED','EXPIRED','EXHAUSTED','BLOCKED') "
            "AND (NOT enabled OR status = 'APPROVED')",
            name="ck_outcome_watch_state",
        ),
    )
    op.create_index(
        "ix_outcome_watch_due", "outcome_watches", ["enabled", "next_run_at", "project_id", "id"]
    )
    op.create_index(
        "ix_outcome_watch_scope_time",
        "outcome_watches",
        ["project_id", "environment_id", "created_at", "id"],
    )
    op.create_table(
        "outcome_watch_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("watch_record_id", sa.Uuid(), nullable=False),
        sa.Column("window_index", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("improvement_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["watch_record_id", "project_id", "environment_id"],
            ["outcome_watches.id", "outcome_watches.project_id", "outcome_watches.environment_id"],
            name="fk_outcome_watch_window_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_outcome_watch_window_snapshot_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["improvement_id", "project_id", "environment_id"],
            [
                "improvement_observations.id",
                "improvement_observations.project_id",
                "improvement_observations.environment_id",
            ],
            name="fk_outcome_watch_window_improvement_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("watch_record_id", "window_index", name="uq_outcome_watch_window"),
        sa.CheckConstraint(
            "window_index >= 0 AND window_index <= 1000000 AND window_start < window_end",
            name="ck_outcome_watch_window_time",
        ),
    )
    op.create_index(
        "ix_outcome_watch_window_page",
        "outcome_watch_windows",
        ["project_id", "environment_id", "watch_record_id", "window_index"],
    )
    op.create_index(
        "ix_outcome_watch_window_snapshot", "outcome_watch_windows", ["candidate_snapshot_id"]
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM outcome_watches)")).scalar():
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_OUTCOME_WATCH_DATA_BEFORE_DOWNGRADE")
    op.drop_table("outcome_watch_windows")
    op.drop_table("outcome_watches")
    op.drop_constraint("uq_improvement_scope", "improvement_observations", type_="unique")
