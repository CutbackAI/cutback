import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_staging_wrapper_disabled_by_default_and_preserves_handler(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "examples/aws-lambda/staging_bootstrap.py"
    spec = importlib.util.spec_from_file_location("synthetic_staging_bootstrap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flushes = []
    module._instance = SimpleNamespace(flush=lambda timeout: flushes.append(timeout))
    event = {"synthetic": True}
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 70)
    handler = module.cutback_invocation(lambda event, ctx: event)
    monkeypatch.delenv("CUTBACK_ENABLED", raising=False)
    assert handler(event, context) is event and not flushes
    monkeypatch.setenv("CUTBACK_ENABLED", "true")
    monkeypatch.setenv("CUTBACK_ENVIRONMENT", "production")
    assert handler(event, context) is event and not flushes
    monkeypatch.setenv("CUTBACK_ENVIRONMENT", "staging")
    assert handler(event, context) is event
    assert flushes == [0.02]

    @module.cutback_invocation
    def failing(event, context):
        raise RuntimeError("synthetic application error")

    with pytest.raises(RuntimeError, match="synthetic application error"):
        failing(event, context)
    assert flushes == [0.02, 0.02]
