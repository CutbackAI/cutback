import os
from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from cutback.server.db import database


@pytest.mark.postgres
def test_migration_downgrade_upgrade_and_model_drift(isolated_postgres_url):
    postgres_url = isolated_postgres_url
    assert "/cutback_test_" in postgres_url  # Only disposable test databases.
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    previous = os.environ.get("CUTBACK_DATABASE_URL")
    os.environ["CUTBACK_DATABASE_URL"] = postgres_url
    try:
        command.downgrade(cfg, "base")
        engine, _ = database(postgres_url)
        assert inspect(engine).get_table_names() == ["alembic_version"]
        engine.dispose()
        command.upgrade(cfg, "head")
        command.check(cfg)
    finally:
        if previous:
            os.environ["CUTBACK_DATABASE_URL"] = previous
