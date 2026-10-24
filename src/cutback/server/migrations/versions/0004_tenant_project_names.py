"""Scope project names to organizations without changing project identities or keys."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("projects_slug_key", "projects", type_="unique")
    op.create_unique_constraint(
        "uq_projects_organization_slug", "projects", ["organization_id", "slug"]
    )


def downgrade():
    duplicates = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM projects GROUP BY slug HAVING count(*) > 1 LIMIT 1"))
        .first()
    )
    if duplicates:
        raise ValueError("DOWNGRADE_REQUIRES_UNIQUE_LEGACY_PROJECT_NAMES")
    op.drop_constraint("uq_projects_organization_slug", "projects", type_="unique")
    op.create_unique_constraint("projects_slug_key", "projects", ["slug"])
