"""Payload-free execution purpose, shared without importing an optional SDK."""

from contextlib import contextmanager
from contextvars import ContextVar

_purpose: ContextVar[dict | None] = ContextVar("cutback_observation_purpose", default=None)


def current_purpose():
    return dict(_purpose.get() or {"traffic_kind": "application"})


@contextmanager
def observation_scope(kind, *, run_id=None):
    if kind not in {"application", "evaluation", "judge"}:
        raise ValueError("unsupported observation purpose")
    value = {**current_purpose(), "traffic_kind": kind}
    if run_id is not None:
        from uuid import UUID

        value["experiment_run_id"] = str(UUID(str(run_id)))
    token = _purpose.set(value)
    try:
        yield
    finally:
        _purpose.reset(token)
