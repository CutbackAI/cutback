"""Separate bounded reactive user calls from target turns within the shared budget."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("ck_episode_turn_budget", "execution_episodes", type_="check")
    op.create_check_constraint(
        "ck_episode_turn_budget",
        "execution_episodes",
        "max_requests >= 1 AND max_requests <= 36 AND requests_used <= max_requests",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM execution_episodes WHERE max_requests > 33) "
                "OR EXISTS (SELECT 1 FROM execution_calls WHERE purpose = 'simulation') "
                "OR EXISTS (SELECT 1 FROM experiment_plans WHERE "
                "specification->'public_specification'->>'evaluator_version' = 'deterministic-v17')"
            )
        )
        .scalar()
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_REACTIVE_USER_DATA_BEFORE_DOWNGRADE")
    op.drop_constraint("ck_episode_turn_budget", "execution_episodes", type_="check")
    op.create_check_constraint(
        "ck_episode_turn_budget",
        "execution_episodes",
        "max_requests >= 1 AND max_requests <= 33 AND requests_used <= max_requests",
    )
