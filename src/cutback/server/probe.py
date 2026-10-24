"""Container-local health checks; credentials never enter argv or HTTP probe headers."""

import argparse
import urllib.request

from sqlalchemy import text

from cutback.server.db import database
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.runtime import configure_database


def probe(*, live_only=False, database_only=False):
    if not database_only:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8080/v1/health/live", timeout=2) as response:
            if response.status != 200:
                raise ValueError("HTTP_NOT_LIVE")
    if not live_only:
        import os

        configure_database()
        engine, _ = database(os.environ["CUTBACK_DATABASE_URL"])
        try:
            with engine.connect() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                if (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    != CURRENT_REVISION
                ):
                    raise ValueError("MIGRATION_REQUIRED")
        finally:
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-only", action="store_true")
    parser.add_argument("--database-only", action="store_true")
    options = parser.parse_args()
    try:
        probe(live_only=options.live_only, database_only=options.database_only)
    except Exception:
        raise SystemExit(1) from None
