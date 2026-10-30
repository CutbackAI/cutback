"""Separate tool dispatch intent, observed effects and guarded result delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tool_sessions", sa.Column("guardrail_policy_id", sa.Uuid()))
    op.add_column("tool_sessions", sa.Column("guardrail_policy_sha256", sa.String(64)))
    op.create_unique_constraint(
        "uq_tool_session_guard_scope", "tool_sessions", ["id", "project_id", "guardrail_policy_id"]
    )
    op.create_foreign_key(
        "fk_tool_session_guard_project",
        "tool_sessions",
        "runtime_guardrail_policies",
        ["guardrail_policy_id", "project_id"],
        ["id", "project_id"],
    )
    op.create_check_constraint(
        "ck_tool_guard_identity_pair",
        "tool_sessions",
        "(guardrail_policy_id IS NULL) = (guardrail_policy_sha256 IS NULL)",
    )
    op.add_column("tool_operations", sa.Column("guardrail_policy_id", sa.Uuid()))
    op.add_column("tool_operations", sa.Column("broker_intent", sa.String(16)))
    op.add_column("tool_operations", sa.Column("result_delivery", sa.String(16)))
    op.create_foreign_key(
        "fk_tool_operation_guard_scope",
        "tool_operations",
        "tool_sessions",
        ["session_id", "project_id", "guardrail_policy_id"],
        ["id", "project_id", "guardrail_policy_id"],
    )
    op.create_check_constraint(
        "ck_tool_broker_intent",
        "tool_operations",
        "broker_intent IS NULL OR broker_intent IN ('NOT_STARTED','STARTED','UNKNOWN')",
    )
    op.create_check_constraint(
        "ck_tool_result_delivery",
        "tool_operations",
        "result_delivery IS NULL OR "
        "result_delivery IN ('NOT_RELEASED','AUTHORIZED','WITHHELD','UNKNOWN')",
    )
    op.create_check_constraint(
        "ck_tool_guard_observation_pair",
        "tool_operations",
        "(guardrail_policy_id IS NULL) = (broker_intent IS NULL) AND "
        "(guardrail_policy_id IS NULL) = (result_delivery IS NULL)",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM tool_sessions WHERE guardrail_policy_id IS NOT NULL")
        )
        .scalar()
    ):
        raise RuntimeError("GUARDED_TOOL_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW")
    for name in (
        "ck_tool_guard_observation_pair",
        "ck_tool_result_delivery",
        "ck_tool_broker_intent",
    ):
        op.drop_constraint(name, "tool_operations", type_="check")
    op.drop_constraint("fk_tool_operation_guard_scope", "tool_operations", type_="foreignkey")
    for name in ("result_delivery", "broker_intent", "guardrail_policy_id"):
        op.drop_column("tool_operations", name)
    op.drop_constraint("ck_tool_guard_identity_pair", "tool_sessions", type_="check")
    op.drop_constraint("fk_tool_session_guard_project", "tool_sessions", type_="foreignkey")
    op.drop_constraint("uq_tool_session_guard_scope", "tool_sessions", type_="unique")
    op.drop_column("tool_sessions", "guardrail_policy_sha256")
    op.drop_column("tool_sessions", "guardrail_policy_id")
