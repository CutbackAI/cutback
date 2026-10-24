"""Long-running self-host worker. Maintenance is available while inference stays disabled."""

import os
import time

from cutback.server.db import database
from cutback.server.operations import maintain_all
from cutback.server.worker import configured_execution_enabled, expire_payloads, run_one


def main():
    engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
    store = None
    if os.getenv("CUTBACK_PAYLOAD_KEY"):
        from cutback.evals.artifacts import configured_store

        store = configured_store()
    last_maintenance = float("-inf")
    try:
        while True:
            try:
                if time.monotonic() - last_maintenance >= 3600:
                    maintain_all(
                        sessions,
                        apply=True,
                        raw_days=int(os.getenv("CUTBACK_RAW_DAYS", "30")),
                        aggregate_days=int(os.getenv("CUTBACK_AGGREGATE_DAYS", "395")),
                    )
                    if store:
                        expire_payloads(sessions, store)
                        from cutback.server.cache_api import expire_cache

                        expire_cache(sessions, store)
                        from cutback.server.training_worker import expire_training_artifacts

                        expire_training_artifacts(sessions, store)
                    last_maintenance = time.monotonic()
                if store and configured_execution_enabled():
                    from cutback.server.continuous import queue_schedules

                    queue_schedules(
                        sessions,
                        enabled=os.getenv("CUTBACK_SCHEDULE_EXECUTION", "false").lower() == "true",
                    )
                    run_one(sessions, store, execution_enabled=True)
                if store and os.getenv("CUTBACK_TRAINING_EXECUTION", "false").lower() == "true":
                    from cutback.server.training_worker import run_training_one

                    run_training_one(sessions, store, execution_enabled=True)
            except Exception:
                print(
                    "Cutback worker cycle failed; inspect service health (details suppressed)",
                    flush=True,
                )
            time.sleep(2)
    finally:
        engine.dispose()
