"""Encrypted supplied-vector cache namespaces and one-use task qualification metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_access_key_project_environment", "access_keys", ["id", "project_id", "environment_id"]
    )
    op.create_unique_constraint(
        "uq_cache_entry_project_scope", "cache_entries", ["id", "project_id"]
    )
    op.create_table(
        "semantic_cache_namespaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("serving_key_id", sa.Uuid(), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("specification_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("active_qualification_id", sa.Uuid(), nullable=True),
        sa.Column("active_approval_hash", sa.String(64), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_policy",
        ),
        sa.ForeignKeyConstraint(
            ["environment_id", "project_id"],
            ["environments.id", "environments.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_environment",
        ),
        sa.ForeignKeyConstraint(
            ["serving_key_id", "project_id", "environment_id"],
            ["access_keys.id", "access_keys.project_id", "access_keys.environment_id"],
            ondelete="CASCADE",
            name="fk_semantic_namespace_key",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_semantic_namespace_project"),
        sa.CheckConstraint(
            "state IN ('DRAFT','ACTIVE','REVOKED') AND "
            "((state = 'ACTIVE') = (active_qualification_id IS NOT NULL)) AND "
            "((state = 'ACTIVE') = (active_approval_hash IS NOT NULL))",
            name="ck_semantic_namespace_activation",
        ),
    )
    op.create_index(
        "ix_semantic_namespace_project_created",
        "semantic_cache_namespaces",
        ["project_id", "created_at", "id"],
    )
    op.create_table(
        "semantic_cache_members",
        sa.Column("cache_entry_id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["cache_entry_id", "project_id"],
            ["cache_entries.id", "cache_entries.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_member_entry",
        ),
        sa.ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_member_namespace",
        ),
    )
    op.create_index(
        "ix_semantic_member_namespace",
        "semantic_cache_members",
        ["project_id", "namespace_id", "cache_entry_id"],
    )
    op.create_table(
        "semantic_cache_qualifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=True),
        sa.Column("selected_threshold", sa.Float(), nullable=True),
        sa.Column("catalog_manifest", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_elapsed_us", sa.BigInteger(), nullable=False),
        sa.Column("active_reserved_us", sa.BigInteger(), nullable=False),
        sa.Column("active_uncertain_us", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_qualification_namespace",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_qualification_dataset",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_semantic_qualification_project"),
        sa.UniqueConstraint(
            "id", "project_id", "namespace_id", name="uq_semantic_qualification_namespace"
        ),
        sa.CheckConstraint(
            "json_typeof(catalog_manifest) = 'array' "
            "AND json_array_length(catalog_manifest) <= 256",
            name="ck_semantic_qualification_catalog_bound",
        ),
        sa.CheckConstraint(
            "active_elapsed_us >= 0 AND active_reserved_us >= 0 "
            "AND active_reserved_us <= 120000000 AND active_uncertain_us >= 0",
            name="ck_semantic_qualification_active_wall",
        ),
    )
    op.create_index(
        "ix_semantic_qualification_eligibility",
        "semantic_cache_qualifications",
        ["state", "created_at", "id"],
    )
    op.create_foreign_key(
        "fk_semantic_namespace_active_qualification",
        "semantic_cache_namespaces",
        "semantic_cache_qualifications",
        ["active_qualification_id", "project_id", "id"],
        ["id", "project_id", "namespace_id"],
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM semantic_cache_namespaces)"))
        .scalar()
    ):
        raise RuntimeError("EXPLICITLY_REVOKE_AND_DELETE_SEMANTIC_CACHE_BEFORE_DOWNGRADE")
    op.drop_constraint(
        "fk_semantic_namespace_active_qualification",
        "semantic_cache_namespaces",
        type_="foreignkey",
    )
    op.drop_table("semantic_cache_qualifications")
    op.drop_table("semantic_cache_members")
    op.drop_table("semantic_cache_namespaces")
    op.drop_constraint("uq_cache_entry_project_scope", "cache_entries", type_="unique")
    op.drop_constraint("uq_access_key_project_environment", "access_keys", type_="unique")
