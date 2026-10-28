"""Destructive migration checks own a separate disposable database."""

import pytest
from test_online_quality import create_quality
from test_online_quality import quality_setup as quality_setup

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_downgrade_refuses_to_erase_quality_ledger_and_releases_migration_lock(
    api, quality_setup, api_database_url, monkeypatch
):
    from importlib.resources import files

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from cutback.server.migrate import CURRENT_REVISION

    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    create_quality(api, quality_setup)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    with pytest.raises(RuntimeError, match="EXPORT_AND_EXPLICITLY_DELETE_QUALITY_PROJECT_DATA"):
        command.downgrade(cfg, "0011")
    with api[2].state.engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_REVISION
        )
        assert connection.scalar(text("SELECT pg_try_advisory_lock(1638366142)")) is True
        connection.execute(text("SELECT pg_advisory_unlock(1638366142)"))
