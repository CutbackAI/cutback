"""Finite application gateway approvals and durable shared spend reservations."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gateway_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("approval_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("state_version", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approval_ref", sa.Uuid(), nullable=True),
        sa.Column("approval_refs", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('PENDING','APPROVED','DISABLED','BLOCKED')", name="ck_gateway_policy_state"
        ),
        sa.CheckConstraint("expires_at > starts_at", name="ck_gateway_policy_window"),
        sa.CheckConstraint(
            "state_version >= 0 AND state_version <= 9007199254740991",
            name="ck_gateway_policy_state_version",
        ),
        sa.CheckConstraint(
            "json_typeof(approval_refs) = 'array' AND json_array_length(approval_refs) <= 1024",
            name="ck_gateway_policy_approval_history_bound",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_gateway_policy_scope"),
        sa.UniqueConstraint("project_id", "policy_id", name="uq_gateway_policy_request"),
    )
    op.create_index(
        "ix_gateway_policy_project_window",
        "gateway_policies",
        ["project_id", "starts_at", "expires_at"],
        unique=False,
    )
    op.create_table(
        "gateway_budgets",
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=10), nullable=False),
        sa.Column("scope_key", sa.String(length=100), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("spent_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("requests_used", sa.BigInteger(), nullable=False),
        sa.Column("active_requests", sa.BigInteger(), nullable=False),
        sa.Column("uncertain_requests", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('project','team','model','key')", name="ck_gateway_budget_kind"
        ),
        sa.CheckConstraint(
            "spent_usd >= 0 AND reserved_usd >= 0 AND requests_used >= 0 "
            "AND active_requests >= 0 AND uncertain_requests >= 0 "
            "AND active_requests + uncertain_requests <= requests_used",
            name="ck_gateway_budget_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["gateway_policies.id", "gateway_policies.project_id"],
            name="fk_gateway_budget_policy_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("policy_id", "scope_kind", "scope_key"),
    )
    op.create_index(
        "ix_gateway_budget_project_policy",
        "gateway_budgets",
        ["project_id", "policy_id"],
        unique=False,
    )
    op.create_table(
        "gateway_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("issued_by", sa.String(length=100), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            name="fk_gateway_key_environment_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["gateway_policies.id", "gateway_policies.project_id"],
            name="fk_gateway_key_policy_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
        sa.UniqueConstraint(
            "id", "policy_id", "project_id", "environment_id", name="uq_gateway_key_scope"
        ),
        sa.UniqueConstraint("project_id", "request_id", name="uq_gateway_key_request"),
    )
    op.create_index(
        "ix_gateway_key_project_policy",
        "gateway_keys",
        ["project_id", "policy_id", "id"],
        unique=False,
    )
    op.create_table(
        "gateway_calls",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("request_hmac", sa.String(length=64), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("traffic_kind", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("price_sha256", sa.String(length=64), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(precision=24, scale=12), nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=24, scale=12), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reserved_usd > 0 AND (cost_usd IS NULL OR cost_usd >= 0) "
            "AND ((state = 'SETTLED' AND cost_usd IS NOT NULL) "
            "OR (state <> 'SETTLED' AND cost_usd IS NULL))",
            name="ck_gateway_call_money",
        ),
        sa.CheckConstraint(
            "state IN ('DISPATCHED','UNCERTAIN','SETTLED')", name="ck_gateway_call_state"
        ),
        sa.CheckConstraint("max_output_tokens BETWEEN 16 AND 16384", name="ck_gateway_call_output"),
        sa.CheckConstraint(
            "traffic_kind IN ('application','evaluation','judge',"
            "'optimization','simulation','unknown')",
            name="ck_gateway_call_purpose",
        ),
        sa.ForeignKeyConstraint(
            ["key_id", "policy_id", "project_id", "environment_id"],
            [
                "gateway_keys.id",
                "gateway_keys.policy_id",
                "gateway_keys.project_id",
                "gateway_keys.environment_id",
            ],
            name="fk_gateway_call_key_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "operation_id"),
    )
    op.create_index(
        "ix_gateway_call_lease",
        "gateway_calls",
        ["state", "lease_until", "project_id"],
        unique=False,
    )
    op.create_index(
        "ix_gateway_call_policy_time",
        "gateway_calls",
        ["project_id", "policy_id", "created_at", "operation_id"],
        unique=False,
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM gateway_policies")):
        raise RuntimeError("EXPLICITLY_DELETE_GATEWAY_DATA_BEFORE_DOWNGRADE")
    op.drop_index("ix_gateway_call_policy_time", table_name="gateway_calls")
    op.drop_index("ix_gateway_call_lease", table_name="gateway_calls")
    op.drop_table("gateway_calls")
    op.drop_index("ix_gateway_key_project_policy", table_name="gateway_keys")
    op.drop_table("gateway_keys")
    op.drop_index("ix_gateway_budget_project_policy", table_name="gateway_budgets")
    op.drop_table("gateway_budgets")
    op.drop_index("ix_gateway_policy_project_window", table_name="gateway_policies")
    op.drop_table("gateway_policies")
