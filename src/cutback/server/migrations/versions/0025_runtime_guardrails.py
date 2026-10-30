"""Scoped runtime guardrail approvals and metadata-only one-shot check intents."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_guardrail_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("capture_policy_id", sa.Uuid(), nullable=False),
        sa.Column("payload_id", sa.Uuid(), nullable=False),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("max_checks", sa.BigInteger(), nullable=False),
        sa.Column("checks_used", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("approved_by", sa.String(100)),
        sa.Column("approval_ref", sa.Uuid()),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["capture_policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            name="fk_runtime_guardrail_capture_scope",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_runtime_guardrail_scope"),
        sa.CheckConstraint("state IN ('PENDING','APPROVED','DISABLED')", name="ck_guardrail_state"),
        sa.CheckConstraint("state_version BETWEEN 0 AND 2", name="ck_guardrail_state_version"),
        sa.CheckConstraint(
            "max_checks BETWEEN 2 AND 10000000 AND checks_used BETWEEN 0 AND max_checks",
            name="ck_guardrail_shared_checks",
        ),
    )
    op.create_index(
        "ix_runtime_guardrail_project", "runtime_guardrail_policies", ["project_id", "id"]
    )
    op.create_table(
        "runtime_guardrail_operations",
        sa.Column("project_id", sa.Uuid(), primary_key=True),
        sa.Column("policy_id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), primary_key=True),
        sa.Column("stage", sa.String(12), primary_key=True),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("decision", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["runtime_guardrail_policies.id", "runtime_guardrail_policies.project_id"],
            ondelete="CASCADE",
            name="fk_runtime_guardrail_operation_scope",
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED','COMPLETED','UNKNOWN')", name="ck_guardrail_check_state"
        ),
        sa.CheckConstraint(
            "stage IN ('input','output','tool_input','tool_output')",
            name="ck_guardrail_check_stage",
        ),
    )
    op.create_index(
        "ix_guardrail_check_project_policy",
        "runtime_guardrail_operations",
        ["project_id", "policy_id", "created_at"],
    )


def downgrade():
    # Intent/approval erasure could make an uncertain application action replayable.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM runtime_guardrail_policies")).scalar():
        raise RuntimeError("RUNTIME_GUARDRAIL_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW")
    op.drop_table("runtime_guardrail_operations")
    op.drop_table("runtime_guardrail_policies")
