from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from cutback.server.economics_db import TaskRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_task_outcome_downgrade_preserves_submitted_evidence(
    api, tenant, api_database_url, monkeypatch
):
    from test_task_outcomes import declaration, ingest

    assert ingest(api, tenant, declaration()).status_code == 200
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_TASK_OUTCOME_DATA_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0013")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.scalar(select(TaskRecord.id)) is not None
