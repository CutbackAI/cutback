"""Keep opt-in native request fingerprints encrypted, with scoped analysis receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("payload_samples", sa.Column("native_profile_ref", sa.String(500), nullable=True))
    op.add_column(
        "payload_samples", sa.Column("native_profile_sha256", sa.String(64), nullable=True)
    )
    op.create_check_constraint(
        "ck_payload_native_profile_pair",
        "payload_samples",
        "(native_profile_ref IS NULL AND native_profile_sha256 IS NULL) OR "
        "(native_profile_ref IS NOT NULL AND native_profile_sha256 IS NOT NULL)",
    )
    op.create_table(
        "native_profile_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Uuid(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(200), nullable=False),
        sa.Column("workflow", sa.String(200), nullable=False),
        sa.Column("requester_actor", sa.String(200), nullable=False),
        sa.Column("purpose", sa.String(200), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("capture_policy_sha256", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_observations", sa.Integer(), nullable=False),
        sa.Column("max_wall_seconds", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(100), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_native_profile_analysis_policy",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_native_profile_analysis_project"),
        sa.CheckConstraint(
            "state IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED','EXPIRED')",
            name="ck_native_profile_analysis_state",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 1 AND max_observations >= 1 AND "
            "max_observations <= 1000 AND max_wall_seconds >= 5 AND max_wall_seconds <= 300",
            name="ck_native_profile_analysis_bounds",
        ),
    )
    op.create_index(
        "ix_payload_native_profile_scan", "payload_samples", ["project_id", "policy_id", "id"]
    )
    op.create_index(
        "ix_native_profile_analysis_queue", "native_profile_analyses", ["state", "created_at", "id"]
    )
    op.create_index(
        "ix_native_profile_analysis_scope",
        "native_profile_analyses",
        ["project_id", "environment", "workflow", "created_at"],
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM native_profile_analyses) OR EXISTS "
            "(SELECT 1 FROM payload_samples WHERE native_profile_ref IS NOT NULL)"
        )
    ).scalar():
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_NATIVE_PROFILES_BEFORE_DOWNGRADE")
    op.drop_table("native_profile_analyses")
    op.drop_index("ix_payload_native_profile_scan", table_name="payload_samples")
    op.drop_constraint("ck_payload_native_profile_pair", "payload_samples", type_="check")
    op.drop_column("payload_samples", "native_profile_sha256")
    op.drop_column("payload_samples", "native_profile_ref")
