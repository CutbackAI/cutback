"""Retained traffic/source dimensions; legacy summaries remain explicitly unknown."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "usage_daily",
        sa.Column("traffic_kind", sa.String(30), nullable=False, server_default="unknown"),
    )
    op.add_column(
        "usage_daily",
        sa.Column("call_site", sa.String(200), nullable=False, server_default="unattributed"),
    )


def downgrade():
    op.drop_column("usage_daily", "call_site")
    op.drop_column("usage_daily", "traffic_kind")
