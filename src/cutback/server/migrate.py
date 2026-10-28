import os
from importlib.resources import files

from alembic import command
from alembic.config import Config

CURRENT_REVISION = "0010"


def migrate(revision="head"):
    if "CUTBACK_DATABASE_URL" not in os.environ:
        raise ValueError("CUTBACK_DATABASE_URL required")
    config = Config()
    config.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    command.upgrade(config, revision)


if __name__ == "__main__":
    from cutback.server.runtime import configure_database

    configure_database()
    migrate()
