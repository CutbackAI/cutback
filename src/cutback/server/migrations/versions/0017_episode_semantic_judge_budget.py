"""One separately reserved semantic judge call beyond the native episode turn cap."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_execution_call_episode_purpose",
        "execution_calls",
        ["project_id", "run_id", "episode_id", "purpose"],
    )
    op.drop_constraint("ck_episode_turn_budget", "execution_episodes", type_="check")
    op.create_check_constraint(
        "ck_episode_turn_budget",
        "execution_episodes",
        "max_requests >= 1 AND max_requests <= 33 AND requests_used <= max_requests",
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM experiment_plans WHERE "
                "specification->'public_specification'->>'evaluator_version' = 'deterministic-v5') "
                "OR EXISTS (SELECT 1 FROM execution_episodes WHERE max_requests > 32)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("EXPORT_AND_EXPLICITLY_DELETE_EPISODE_JUDGE_DATA_BEFORE_DOWNGRADE")
    op.drop_index("ix_execution_call_episode_purpose", table_name="execution_calls")
    op.drop_constraint("ck_episode_turn_budget", "execution_episodes", type_="check")
    op.create_check_constraint(
        "ck_episode_turn_budget",
        "execution_episodes",
        "max_requests >= 1 AND max_requests <= 32 AND requests_used <= max_requests",
    )
