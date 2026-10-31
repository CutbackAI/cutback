"""Retain observed model identifiers separately from requested configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade():
    # Old receipts deliberately stay NULL; requested models cannot fill this gap.
    op.add_column("execution_calls", sa.Column("response_model", sa.String(200), nullable=True))


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM execution_calls WHERE response_model IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_OBSERVED_MODEL_EVIDENCE_BEFORE_DOWNGRADE")
    op.drop_column("execution_calls", "response_model")
