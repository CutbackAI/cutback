"""Nullable native stream identity and observed gateway first-text timing."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy observations stay unknown: do not backfill fabricated timing/shape.
    op.add_column("gateway_calls", sa.Column("streaming", sa.Boolean(), nullable=True))
    op.add_column("gateway_calls", sa.Column("first_text_delta_us", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_gateway_stream_timing",
        "gateway_calls",
        "first_text_delta_us IS NULL OR (streaming IS TRUE "
        "AND first_text_delta_us BETWEEN 0 AND 120000000)",
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM gateway_calls WHERE streaming IS TRUE "
            "OR first_text_delta_us IS NOT NULL) OR EXISTS "
            "(SELECT 1 FROM gateway_policies WHERE "
            "specification->>'version' = 'cutback.gateway-policy.v2')"
        )
    ).scalar():
        raise RuntimeError(
            "EXPLICITLY_DELETE_GATEWAY_DATA_BEFORE_DOWNGRADE: "
            "GATEWAY_STREAMING_INTENT_OR_EVIDENCE_REQUIRES_REVIEWED_RETENTION_OR_DELETION"
        )
    op.drop_constraint("ck_gateway_stream_timing", "gateway_calls", type_="check")
    op.drop_column("gateway_calls", "first_text_delta_us")
    op.drop_column("gateway_calls", "streaming")
