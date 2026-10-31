"""Keep cache deliveries distinct from fresh native requests and historical model identity."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade():
    # Old records stay unknown. Zero tokens alone cannot establish a cache hit.
    op.add_column("execution_calls", sa.Column("cache_observation", sa.JSON(), nullable=True))


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM execution_calls WHERE cache_observation IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_CACHE_CALL_EVIDENCE_BEFORE_DOWNGRADE")
    op.drop_column("execution_calls", "cache_observation")
