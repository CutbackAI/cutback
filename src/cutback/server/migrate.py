import os
from importlib.resources import files

from alembic import command
from alembic.config import Config


def migrate(revision="head"):
    if "CUTBACK_DATABASE_URL" not in os.environ:
        raise ValueError("CUTBACK_DATABASE_URL required")
    config = Config()
    config.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    command.upgrade(config, revision)
