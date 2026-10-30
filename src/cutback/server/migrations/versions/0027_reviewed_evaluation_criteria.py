"""Generated evaluation criteria, owner review and exact experiment bindings."""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "evaluation_criteria_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("capture_policy_id", sa.Uuid(), nullable=False),
        sa.Column("generation_plan_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("generation_run_id", sa.Uuid(), unique=True),
        sa.Column("input_payload_id", sa.Uuid(), nullable=False),
        sa.Column("output_payload_id", sa.Uuid()),
        sa.Column("source_dataset_sha256", sa.String(64), nullable=False),
        sa.Column("generation_plan_sha256", sa.String(64), nullable=False),
        sa.Column("version_sha256", sa.String(64)),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("review_status", sa.String(12), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("generation_approved_by", sa.String(100)),
        sa.Column("reviewed_by", sa.String(100)),
        sa.Column("review_approval_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "project_id", name="uq_criteria_version_project"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            name="fk_criteria_dataset_scope",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_criteria_environment_scope",
        ),
        sa.ForeignKeyConstraint(
            ["capture_policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_criteria_capture_scope",
        ),
        sa.ForeignKeyConstraint(
            ["generation_plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            name="fk_criteria_plan_scope",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_criteria_run_scope",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_criteria_generation_state",
        ),
        sa.CheckConstraint(
            "review_status IN ('PENDING','APPROVED','REJECTED')", name="ck_criteria_review_state"
        ),
        sa.CheckConstraint(
            "state != 'COMPLETED' OR (generation_run_id IS NOT NULL "
            "AND output_payload_id IS NOT NULL AND version_sha256 IS NOT NULL)",
            name="ck_criteria_completed_proposal",
        ),
        sa.CheckConstraint(
            "review_status = 'PENDING' OR (state = 'COMPLETED' "
            "AND version_sha256 IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_approval_id IS NOT NULL)",
            name="ck_criteria_review_requires_completed_content",
        ),
    )
    op.create_index(
        "ix_criteria_project_created",
        "evaluation_criteria_versions",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_evaluation_criteria_versions_expires_at", "evaluation_criteria_versions", ["expires_at"]
    )
    op.create_table(
        "evaluation_criteria_uses",
        sa.Column("project_id", sa.Uuid(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), primary_key=True),
        sa.Column("criteria_id", sa.Uuid(), nullable=False),
        sa.Column("version_sha256", sa.String(64), nullable=False),
        sa.Column("review_approval_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["criteria_id", "project_id"],
            ["evaluation_criteria_versions.id", "evaluation_criteria_versions.project_id"],
            name="fk_criteria_use_version_scope",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            ondelete="CASCADE",
            name="fk_criteria_use_plan_scope",
        ),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM evaluation_criteria_versions")).scalar():
        raise RuntimeError("CRITERIA_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW")
    op.drop_table("evaluation_criteria_uses")
    op.drop_index(
        "ix_evaluation_criteria_versions_expires_at", table_name="evaluation_criteria_versions"
    )
    op.drop_index("ix_criteria_project_created", table_name="evaluation_criteria_versions")
    op.drop_table("evaluation_criteria_versions")
