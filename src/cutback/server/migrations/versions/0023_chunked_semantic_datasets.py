"""Resumable encrypted dataset uploads without widening the collector request cap."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "semantic_dataset_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("namespace_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), nullable=True),
        sa.Column("specification", sa.JSON(), nullable=False),
        sa.Column("approval_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("expected_cases", sa.Integer(), nullable=False),
        sa.Column("received_cases", sa.Integer(), nullable=False),
        sa.Column("max_bytes", sa.Integer(), nullable=False),
        sa.Column("received_bytes", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("uncertain_attempts", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(120), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["namespace_id", "project_id"],
            ["semantic_cache_namespaces.id", "semantic_cache_namespaces.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_namespace",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id", "project_id"],
            ["capture_policies.id", "capture_policies.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_policy",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "project_id"],
            ["datasets.id", "datasets.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_dataset",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_semantic_upload_project"),
        sa.CheckConstraint(
            "expected_cases >= 1 AND expected_cases <= 512 AND received_cases >= 0 "
            "AND received_cases <= expected_cases AND max_bytes >= 1 AND max_bytes <= 33554432 "
            "AND received_bytes >= 0 AND received_bytes <= max_bytes",
            name="ck_semantic_upload_bounds",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 2 AND uncertain_attempts >= 0 "
            "AND uncertain_attempts <= attempts",
            name="ck_semantic_upload_attempts",
        ),
    )
    op.create_index(
        "ix_semantic_upload_policy_expiry",
        "semantic_dataset_uploads",
        ["policy_id", "state", "expires_at"],
    )
    op.create_table(
        "semantic_upload_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("byte_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["upload_id", "project_id"],
            ["semantic_dataset_uploads.id", "semantic_dataset_uploads.project_id"],
            ondelete="CASCADE",
            name="fk_semantic_upload_case_parent",
        ),
        sa.ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            name="fk_semantic_upload_case_payload",
        ),
        sa.UniqueConstraint("upload_id", "ordinal", name="uq_semantic_upload_ordinal"),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal < 512 AND byte_count > 0 AND byte_count <= 1048576",
            name="ck_semantic_upload_case_bound",
        ),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM semantic_dataset_uploads)"))
        .scalar()
    ):
        raise RuntimeError("EXPLICITLY_CANCEL_OR_REMOVE_DATASET_UPLOADS_BEFORE_DOWNGRADE")
    op.drop_table("semantic_upload_cases")
    op.drop_table("semantic_dataset_uploads")
