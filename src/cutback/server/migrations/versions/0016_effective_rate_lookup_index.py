"""Index the project/provider/model/effective-date lookup without changing rates."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_rate_override_effective_lookup",
        "rate_overrides",
        ["project_id", "provider", "model", sa.text("valid_from DESC")],
    )


def downgrade():
    op.drop_index("ix_rate_override_effective_lookup", table_name="rate_overrides")
