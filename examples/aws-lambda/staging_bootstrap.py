"""Optional staging-only Cutback observation; preserves the existing SDK/dependency pins."""

import functools
import logging
import os

_instance = None


def _observer():
    global _instance
    if (
        os.getenv("CUTBACK_ENABLED", "false").lower() != "true"
        or os.getenv("CUTBACK_ENVIRONMENT") != "staging"
    ):
        return None
    if _instance is None:
        import cutback

        _instance = cutback.init(
            service_name=os.environ["CUTBACK_SERVICE_NAME"],
            environment="staging",
            enabled=True,
            providers=("openai",),
            capture_content=False,
            timeout_seconds=0.15,
            capacity=128,
            batch_size=20,
        )
    return _instance


def cutback_invocation(handler):
    @functools.wraps(handler)
    def invoke(event, context):
        observer = None
        try:
            observer = _observer()
        except Exception:
            logging.getLogger(__name__).warning("Cutback init failed; application continues")
        try:
            return handler(event, context)
        finally:
            if observer is not None:
                try:
                    remaining = getattr(context, "get_remaining_time_in_millis", lambda: 1000)()
                    observer.flush(min(0.1, max(0, (remaining - 50) / 1000)))
                except Exception:
                    pass

    return invoke
