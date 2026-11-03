"""Bind saved view environments to their project; preserve typed analytics on rollback."""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "fk_saved_view_environment_project",
        "saved_views",
        "environments",
        ["environment_id", "project_id"],
        ["id", "project_id"],
        ondelete="CASCADE",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM saved_views "
            "WHERE specification->>'page' = 'analytics_dashboard')"
        )
    ):
        raise ValueError("ANALYTICS_EXPORT_AND_REMOVE_BEFORE_DOWNGRADE")
    op.drop_constraint("fk_saved_view_environment_project", "saved_views", type_="foreignkey")
