"""Long-running self-host worker. Maintenance is available while inference stays disabled."""

import os
import time

from cutback.server.db import database
from cutback.server.operations import maintain_all
from cutback.server.worker import configured_execution_enabled, expire_payloads, run_one


def worker_cycle(sessions, store, *, maintenance_due=True):
    """Independent failure domains: maintenance cannot starve an eligible job."""
    outcomes = {}

    def attempt(phase, action):
        try:
            result = action()
            outcomes[phase] = {
                "status": "FAILED" if isinstance(result, dict) and result.get("failures") else "OK",
                "result": result,
            }
        except Exception:
            outcomes[phase] = {"status": "FAILED", "code": "WORKER_PHASE_FAILED"}
            print(f"Cutback worker phase {phase} failed; details suppressed", flush=True)

    if maintenance_due:
        from cutback.server.campaign_retention import expire_campaigns
        from cutback.server.execution_ledger import recover_execution_leases

        attempt("execution_recovery", lambda: recover_execution_leases(sessions))
        if any(
            os.getenv(flag, "false").lower() == "true"
            for flag in ("CUTBACK_TOOL_EXECUTION", "CUTBACK_TOOL_CONTAINMENT")
        ):
            from cutback.server.tool_recovery import recover_tool_one

            attempt("tool_containment", lambda: recover_tool_one(sessions))
        attempt("campaign_expiry", lambda: expire_campaigns(sessions))
        attempt(
            "maintenance",
            lambda: maintain_all(
                sessions,
                apply=True,
                raw_days=int(os.getenv("CUTBACK_RAW_DAYS", "30")),
                aggregate_days=int(os.getenv("CUTBACK_AGGREGATE_DAYS", "395")),
            ),
        )
        if store:
            from cutback.server.cache_api import expire_cache
            from cutback.server.training_worker import expire_training_artifacts

            attempt("payload_expiry", lambda: expire_payloads(sessions, store))
            attempt("cache_expiry", lambda: expire_cache(sessions, store))
            attempt("training_expiry", lambda: expire_training_artifacts(sessions, store))
    if store and configured_execution_enabled():
        from cutback.server.campaign_worker import advance_one
        from cutback.server.continuous import queue_schedules
        from cutback.server.proposal_worker import run_proposal_one

        attempt("campaigns", lambda: advance_one(sessions, store, execution_enabled=True))
        attempt("proposals", lambda: run_proposal_one(sessions, store, execution_enabled=True))

        attempt(
            "schedules",
            lambda: queue_schedules(
                sessions, enabled=os.getenv("CUTBACK_SCHEDULE_EXECUTION", "false").lower() == "true"
            ),
        )
        attempt("experiments", lambda: run_one(sessions, store, execution_enabled=True))
    if store and os.getenv("CUTBACK_BATCH_EXECUTION", "false").lower() == "true":
        from cutback.server.provider_batch_worker import run_batch_one

        attempt("provider_batches", lambda: run_batch_one(sessions, store, execution_enabled=True))
    if store and os.getenv("CUTBACK_SEMANTIC_CACHE_EXECUTION", "false").lower() == "true":
        from cutback.server.semantic_cache_worker import run_semantic_cache_one
        from cutback.server.semantic_upload_worker import run_semantic_upload_one

        attempt(
            "semantic_dataset_uploads",
            lambda: run_semantic_upload_one(sessions, store, execution_enabled=True),
        )
        attempt(
            "semantic_qualifications",
            lambda: run_semantic_cache_one(sessions, store, execution_enabled=True),
        )
    if os.getenv("CUTBACK_OUTCOME_WATCH_EXECUTION", "false").lower() == "true":
        from cutback.server.outcome_watches import run_outcome_watch_one

        attempt("outcome_watches", lambda: run_outcome_watch_one(sessions, enabled=True))
    if os.getenv("CUTBACK_ONLINE_EXECUTION", "false").lower() == "true":
        from cutback.server.incidents import evaluate_alerts
        from cutback.server.monitoring import backfill_one, queue_online_intake, run_online_one

        attempt("online_intake", lambda: queue_online_intake(sessions, enabled=True))
        attempt("online_backfill", lambda: backfill_one(sessions, enabled=True))
        attempt("online_evaluations", lambda: run_online_one(sessions, enabled=True))
        attempt("online_alerts", lambda: evaluate_alerts(sessions, enabled=True))
    if os.getenv("CUTBACK_WEBHOOK_EXECUTION", "false").lower() == "true":
        from cutback.server.notifications import run_notification_one

        attempt("notifications", lambda: run_notification_one(sessions, enabled=True))
    if store and os.getenv("CUTBACK_QUALITY_EXECUTION", "false").lower() == "true":
        from cutback.server.quality_service import backfill_quality_one
        from cutback.server.quality_worker import run_quality_one

        attempt("quality_backfill", lambda: backfill_quality_one(sessions, enabled=True))
        attempt(
            "quality_evaluations", lambda: run_quality_one(sessions, store, execution_enabled=True)
        )
    if store and os.getenv("CUTBACK_TRAINING_EXECUTION", "false").lower() == "true":
        from cutback.server.training_worker import run_training_one

        attempt("training", lambda: run_training_one(sessions, store, execution_enabled=True))
    return outcomes


def main():
    engine, sessions = database(os.environ["CUTBACK_DATABASE_URL"])
    store = None
    if os.getenv("CUTBACK_PAYLOAD_KEY"):
        from cutback.evals.artifacts import configured_store

        store = configured_store()
    next_maintenance = float("-inf")
    try:
        while True:
            due = time.monotonic() >= next_maintenance
            outcomes = worker_cycle(sessions, store, maintenance_due=due)
            if due:
                failed = any(value["status"] == "FAILED" for value in outcomes.values())
                next_maintenance = time.monotonic() + (60 if failed else 3600)
            time.sleep(2)
    finally:
        engine.dispose()
