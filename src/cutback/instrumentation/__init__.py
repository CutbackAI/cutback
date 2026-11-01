import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_identity: ContextVar[dict | None] = ContextVar("cutback_identity", default=None)
_active = None
_lock = threading.Lock()


@contextmanager
def workflow(workflow_id: str, *, call_site_id: str = "unattributed"):
    """Explicit attribution; use scanner IDs. Never derive workflow identity from user content."""
    identity = {"workflow_id": workflow_id}
    if call_site_id != "unattributed":
        identity["call_site_id"] = call_site_id
    token = _identity.set(identity)
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


class Observers:
    enabled = True

    def __init__(self, observers, identity, policy_runtime=None):
        self.observers, self.identity = observers, identity
        self.policy_runtime = policy_runtime
        self.closed = False

    def flush(self, timeout_seconds=0.3):
        import time

        deadline = time.monotonic() + min(max(timeout_seconds, 0), 5)
        return all(
            [observer.flush(max(0, deadline - time.monotonic())) for observer in self.observers]
        )

    def health(self):
        values = [observer.health() for observer in self.observers]
        result: dict[str, Any] = {"enabled": not self.closed, "providers": values}
        for value in values:
            for key, count in value.items():
                if isinstance(count, int) and not isinstance(count, bool):
                    result[key] = result.get(key, 0) + count
        return result

    def close(self, timeout_seconds=0.3):
        import time

        self.closed = True
        deadline = time.monotonic() + min(max(timeout_seconds, 0), 5)
        return all(
            [observer.close(max(0, deadline - time.monotonic())) for observer in self.observers]
        )


def init(
    *,
    service_name: str,
    environment: str,
    endpoint: str | None = None,
    project_id: str | None = None,
    project_key_from_env: str = "CUTBACK_PROJECT_TOKEN",
    capture_content: bool = False,
    capture_policy_id: str | None = None,
    enabled: bool | None = None,
    providers: tuple[str, ...] | None = None,
    source_map: str | None = None,
    observe_policy_runtime: bool = False,
    **export_options,
):
    """Initialize once per process, outside Lambda handler. Explicit shutdown restores SDK hooks."""
    global _active
    if capture_content and not capture_policy_id:
        raise ValueError("content capture requires an approved capture_policy_id")
    enabled = (
        enabled if enabled is not None else os.getenv("CUTBACK_ENABLED", "false").lower() == "true"
    )
    if not enabled:
        with _lock:
            if _active is not None and not _active.closed:
                _active.close()
        return Disabled()
    providers = providers or tuple(os.getenv("CUTBACK_PROVIDERS", "openai").split(","))
    if (
        not providers
        or len(set(providers)) != len(providers)
        or set(providers) - {"openai", "anthropic"}
    ):
        raise ValueError("providers must uniquely name openai and/or anthropic")
    from cutback.instrumentation.anthropic import AnthropicInstrumentation
    from cutback.instrumentation.openai import OpenAIInstrumentation

    source_map = source_map or os.getenv("CUTBACK_SOURCE_MAP")
    from cutback.instrumentation.attribution import SourceMap

    attribution = SourceMap(source_map) if source_map else None

    identity = (
        service_name,
        environment,
        endpoint,
        project_id,
        providers,
        capture_content,
        capture_policy_id,
        source_map,
        observe_policy_runtime,
    )

    with _lock:
        if _active is not None:
            if _active.closed:
                _active = None
            else:
                if _active.identity != identity:
                    raise ValueError("Cutback already initialized with different configuration")
                return _active
        observers: list[Any] = []
        try:
            for provider in providers:
                cls = OpenAIInstrumentation if provider == "openai" else AnthropicInstrumentation
                capture = None
                if capture_content:
                    from cutback.instrumentation.capture import CaptureExporter

                    capture = CaptureExporter(
                        endpoint or os.environ["CUTBACK_ENDPOINT"],
                        project_id or os.environ["CUTBACK_PROJECT_ID"],
                        os.environ.get(project_key_from_env, ""),
                        capture_policy_id,
                        timeout=export_options.get("timeout_seconds", 0.3),
                    )
                try:
                    observer = cls(
                        service_name=service_name,
                        environment=environment,
                        endpoint=endpoint or os.environ["CUTBACK_ENDPOINT"],
                        project_id=project_id or os.environ["CUTBACK_PROJECT_ID"],
                        token=os.environ.get(project_key_from_env, ""),
                        identity=identity,
                        capture=capture,
                        attribution=attribution,
                        **export_options,
                    )
                except BaseException:
                    if capture is not None:
                        capture.close()
                    raise
                observers.append(observer)
            activity = None
            if observe_policy_runtime:
                from cutback.instrumentation.policy_activity import PolicyActivityObserver

                activity = PolicyActivityObserver(
                    service_name=service_name,
                    environment=environment,
                    endpoint=endpoint or os.environ["CUTBACK_ENDPOINT"],
                    project_id=project_id or os.environ["CUTBACK_PROJECT_ID"],
                    token=os.environ.get(project_key_from_env, ""),
                    attribution=attribution,
                    **export_options,
                )
                observers.append(activity)
        except BaseException:
            for observer in observers:
                observer.close()
            raise
        _active = observers[0] if len(observers) == 1 else Observers(observers, identity, activity)
        return _active


def _begin_policy_activity(**metadata):
    observer = getattr(_active, "policy_runtime", None)
    if observer is None or observer.closed:
        return None
    try:
        token = observer.begin(**metadata)
        return (observer, token) if token is not None else None
    except Exception:
        observer.count("normalization_failed")
        return None


def _finish_policy_activity(activity, *, completion, receipt=None):
    if activity is None:
        return
    observer, token = activity
    try:
        observer.finish(token, completion=completion, receipt=receipt)
    except Exception:
        observer.count("normalization_failed")
