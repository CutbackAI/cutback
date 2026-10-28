"""Scoped versioned application tasks and externally asserted cost components."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_change_project_identity", "change_proposals", ["id", "project_id"]
    )
    op.create_table(
        "application_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(32), nullable=False),
        sa.Column("workflow", sa.String(200), nullable=False),
        sa.Column("service", sa.String(200), nullable=False),
        sa.Column("release", sa.String(200), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_task_environment_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "project_id", "environment_id", name="uq_task_record_scope"),
        sa.UniqueConstraint(
            "project_id", "environment_id", "task_id", name="uq_task_external_identity"
        ),
        sa.UniqueConstraint(
            "project_id", "environment_id", "trace_id", name="uq_task_trace_allocation"
        ),
        sa.CheckConstraint(
            "current_revision >= 0 AND current_revision <= 100", name="ck_task_revision"
        ),
    )
    op.create_index(
        "ix_task_scope_time",
        "application_tasks",
        ["project_id", "environment_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_task_release_time",
        "application_tasks",
        ["project_id", "environment_id", "workflow", "release", "occurred_at"],
    )
    op.create_table(
        "application_task_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("task_record_id", sa.Uuid(), nullable=False),
        sa.Column("result_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("metadata_record", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["task_record_id", "project_id", "environment_id"],
            [
                "application_tasks.id",
                "application_tasks.project_id",
                "application_tasks.environment_id",
            ],
            name="fk_task_result_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id", "environment_id", "result_id", name="uq_task_result_external_identity"
        ),
        sa.UniqueConstraint("task_record_id", "revision", name="uq_task_result_revision"),
        sa.CheckConstraint("revision >= 1 AND revision <= 100", name="ck_task_result_revision"),
    )
    op.create_index(
        "ix_task_result_scope_time",
        "application_task_results",
        ["project_id", "environment_id", "received_at", "id"],
    )
    op.create_table(
        "task_cost_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_task_snapshot_environment_scope",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "project_id", "environment_id", name="uq_task_snapshot_scope"),
        sa.UniqueConstraint("project_id", "snapshot_id", name="uq_task_snapshot_request"),
    )
    op.create_index(
        "ix_task_snapshot_scope_time",
        "task_cost_snapshots",
        ["project_id", "environment_id", "created_at", "id"],
    )
    op.create_table(
        "improvement_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("change_id", sa.Uuid(), nullable=True),
        sa.Column("baseline_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_improvement_environment_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["change_id", "project_id"],
            ["change_proposals.id", "change_proposals.project_id"],
            name="fk_improvement_change_scope",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_improvement_baseline_scope",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_snapshot_id", "project_id", "environment_id"],
            [
                "task_cost_snapshots.id",
                "task_cost_snapshots.project_id",
                "task_cost_snapshots.environment_id",
            ],
            name="fk_improvement_candidate_scope",
        ),
        sa.UniqueConstraint(
            "project_id", "observation_id", name="uq_improvement_observation_request"
        ),
        sa.CheckConstraint(
            "baseline_snapshot_id != candidate_snapshot_id",
            name="ck_improvement_distinct_snapshots",
        ),
    )
    op.create_index(
        "ix_improvement_scope_time",
        "improvement_observations",
        ["project_id", "environment_id", "created_at", "id"],
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM application_tasks) OR EXISTS "
                "(SELECT 1 FROM application_task_results) OR EXISTS "
                "(SELECT 1 FROM task_cost_snapshots) "
                "OR EXISTS (SELECT 1 FROM improvement_observations)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_TASK_OUTCOME_DATA_BEFORE_DOWNGRADE")
    op.drop_index("ix_improvement_scope_time", table_name="improvement_observations")
    op.drop_table("improvement_observations")
    op.drop_index("ix_task_snapshot_scope_time", table_name="task_cost_snapshots")
    op.drop_table("task_cost_snapshots")
    op.drop_index("ix_task_result_scope_time", table_name="application_task_results")
    op.drop_table("application_task_results")
    op.drop_index("ix_task_release_time", table_name="application_tasks")
    op.drop_index("ix_task_scope_time", table_name="application_tasks")
    op.drop_table("application_tasks")
    op.drop_constraint("uq_change_project_identity", "change_proposals", type_="unique")
