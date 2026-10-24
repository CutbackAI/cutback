import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_identity: ContextVar[dict | None] = ContextVar("cutback_identity", default=None)
_active = None
_lock = threading.Lock()


@contextmanager
def workflow(workflow_id: str, *, call_site_id: str = "unattributed"):
    """Explicit attribution; use scanner IDs. Never derive workflow identity from user content."""
    token = _identity.set({"workflow_id": workflow_id, "call_site_id": call_site_id})
    try:
        yield
    finally:
        _identity.reset(token)


class Disabled:
    enabled = False

    def health(self):
        return {"enabled": False, "reason": "explicitly_disabled"}

    def flush(self, timeout_seconds=0.3):
        return True

    def close(self, timeout_seconds=0.3):
        return True


def init(
    *,
    service_name: str,
    environment: str,
    endpoint: str | None = None,
    project_id: str | None = None,
    project_key_from_env: str = "CUTBACK_PROJECT_TOKEN",
    capture_content: bool = False,
    enabled: bool | None = None,
    **export_options,
):
    """Initialize once per process, outside Lambda handler. Explicit shutdown restores SDK hooks."""
    global _active
    if capture_content:
        raise ValueError("content capture is unavailable; no payload will be collected")
    enabled = (
        enabled if enabled is not None else os.getenv("CUTBACK_ENABLED", "false").lower() == "true"
    )
    if not enabled:
        with _lock:
            if _active is not None and not _active.closed:
                _active.close()
        return Disabled()
    from cutback.instrumentation.openai import OpenAIInstrumentation

    with _lock:
        if _active is not None:
            if _active.closed:
                _active = None
            else:
                if _active.identity != (service_name, environment, endpoint, project_id):
                    raise ValueError("Cutback already initialized with different configuration")
                return _active
        _active = OpenAIInstrumentation(
            service_name=service_name,
            environment=environment,
            endpoint=endpoint or os.environ["CUTBACK_ENDPOINT"],
            project_id=project_id or os.environ["CUTBACK_PROJECT_ID"],
            token=os.environ.get(project_key_from_env, ""),
            identity=(service_name, environment, endpoint, project_id),
            **export_options,
        )
        return _active
