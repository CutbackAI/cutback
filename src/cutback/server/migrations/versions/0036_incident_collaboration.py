"""Encrypted incident comments and bounded immutable revision metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_incident_project_scope", "incidents", ["id", "project_id"])
    op.create_table(
        "incident_comments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("creator", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("deleted_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision BETWEEN 1 AND 51", name="ck_incident_comment_revision"),
        sa.CheckConstraint(
            "(deleted = false AND deleted_by IS NULL AND current_version_id IS NOT NULL) OR "
            "(deleted = true AND deleted_by IS NOT NULL AND current_version_id IS NULL)",
            name="ck_incident_comment_tombstone",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["incident_id", "project_id"],
            ["incidents.id", "incidents.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_parent",
        ),
        sa.UniqueConstraint("id", "project_id", name="uq_incident_comment_scope"),
    )
    op.create_index(
        "ix_incident_comment_page",
        "incident_comments",
        ["project_id", "incident_id", "created_at", "id"],
    )
    op.create_table(
        "incident_comment_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("comment_id", sa.Uuid(), nullable=False),
        sa.Column("payload_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.CheckConstraint(
            "revision BETWEEN 1 AND 50", name="ck_incident_comment_version_revision"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["comment_id", "project_id"],
            ["incident_comments.id", "incident_comments.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_version_parent",
        ),
        sa.ForeignKeyConstraint(
            ["payload_id", "project_id"],
            ["payload_samples.id", "payload_samples.project_id"],
            ondelete="CASCADE",
            name="fk_incident_comment_version_payload",
        ),
        sa.UniqueConstraint("comment_id", "revision", name="uq_incident_comment_revision"),
    )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM incident_comments)")):
        raise ValueError("INCIDENT_COMMENTS_EXPORT_AND_REMOVE_BEFORE_DOWNGRADE")
    op.drop_table("incident_comment_versions")
    op.drop_table("incident_comments")
    op.drop_constraint("uq_incident_project_scope", "incidents", type_="unique")
