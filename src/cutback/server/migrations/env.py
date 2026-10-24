import os

from alembic import context

from cutback.server.db import Base, database

target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url="postgresql+psycopg://", target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine, _ = database(os.environ["CUTBACK_DATABASE_URL"])
    try:
        with engine.connect() as connection:
            from sqlalchemy import text

            connection.execute(text("SELECT pg_advisory_lock(1638366142)"))
            connection.commit()
            try:
                context.configure(connection=connection, target_metadata=target_metadata)
                with context.begin_transaction():
                    context.run_migrations()
            finally:
                # A refused/failed migration must not strand the session-level lock.
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(1638366142)"))
                connection.commit()
    finally:
        engine.dispose()
