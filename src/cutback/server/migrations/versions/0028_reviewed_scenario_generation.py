"""Reviewed generated scenarios and current-epoch development dataset bindings."""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "generated_scenario_versions",
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
        sa.UniqueConstraint("id", "project_id", name="uq_scenario_version_project"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            name="fk_scenario_source_dataset_scope",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_scenario_environment_scope",
        ),
        sa.ForeignKeyConstraint(
            ["capture_policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_scenario_capture_scope",
        ),
        sa.ForeignKeyConstraint(
            ["generation_plan_id", "project_id"],
            ["experiment_plans.id", "experiment_plans.project_id"],
            name="fk_scenario_generation_plan_scope",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id", "project_id"],
            ["experiment_runs.id", "experiment_runs.project_id"],
            name="fk_scenario_generation_run_scope",
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name="ck_scenario_generation_state",
        ),
        sa.CheckConstraint(
            "review_status IN ('PENDING','APPROVED','REJECTED')", name="ck_scenario_review_state"
        ),
        sa.CheckConstraint(
            "state != 'COMPLETED' OR (generation_run_id IS NOT NULL "
            "AND output_payload_id IS NOT NULL AND version_sha256 IS NOT NULL)",
            name="ck_scenario_completed_content",
        ),
        sa.CheckConstraint(
            "review_status = 'PENDING' OR (state = 'COMPLETED' "
            "AND version_sha256 IS NOT NULL AND reviewed_by IS NOT NULL "
            "AND reviewed_at IS NOT NULL AND review_approval_id IS NOT NULL)",
            name="ck_scenario_review_requires_completed_content",
        ),
    )
    op.create_index(
        "ix_scenario_project_created",
        "generated_scenario_versions",
        ["project_id", "created_at", "id"],
    )
    op.create_index(
        "ix_generated_scenario_versions_expires_at", "generated_scenario_versions", ["expires_at"]
    )
    op.create_index(
        "ix_scenario_policy_outputs",
        "generated_scenario_versions",
        ["capture_policy_id", "state", "expires_at"],
    )
    op.create_table(
        "generated_scenario_imports",
        sa.Column("project_id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), primary_key=True),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("version_sha256", sa.String(64), nullable=False),
        sa.Column("review_approval_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["scenario_id", "project_id"],
            ["generated_scenario_versions.id", "generated_scenario_versions.project_id"],
            name="fk_scenario_import_version_scope",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            ondelete="CASCADE",
            name="fk_scenario_import_dataset_scope",
        ),
        sa.UniqueConstraint("request_id", name="uq_scenario_import_request_identity"),
    )
    op.create_index(
        "ix_scenario_import_source", "generated_scenario_imports", ["scenario_id", "project_id"]
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM generated_scenario_versions")).scalar():
        raise RuntimeError("SCENARIO_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW")
    op.drop_table("generated_scenario_imports")
    op.drop_index("ix_scenario_policy_outputs", table_name="generated_scenario_versions")
    op.drop_index(
        "ix_generated_scenario_versions_expires_at", table_name="generated_scenario_versions"
    )
    op.drop_index("ix_scenario_project_created", table_name="generated_scenario_versions")
    op.drop_table("generated_scenario_versions")
