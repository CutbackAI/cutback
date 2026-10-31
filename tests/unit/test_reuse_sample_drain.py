"""The no-cost sample waits for queued exports without replaying dropped requests."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def sample():
    path = Path(__file__).resolve().parents[2] / "examples/request-reuse/demo.py"
    spec = importlib.util.spec_from_file_location("owned_reuse_drain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_slow_queued_capture_drains_without_another_application_call(monkeypatch):
    module = sample()
    clock = [0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Observer:
        calls = 0

        def flush(self, timeout):
            self.calls += 1
            clock[0] += timeout
            return self.calls == 2

        def health(self):
            return {"native_profile_queued": 8, "native_profile_exported": 4 * self.calls}

    observer = Observer()
    module.require_profile_capture(observer, 8)
    assert observer.calls == 2 and clock[0] == 10


@pytest.mark.parametrize("dropped", [False, True])
def test_missing_or_dropped_capture_abstains_with_only_numeric_diagnostics(monkeypatch, dropped):
    module = sample()
    clock = [0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Observer:
        calls = 0

        def flush(self, timeout):
            self.calls += 1
            clock[0] += timeout
            return False

        def health(self):
            return {
                "native_profile_queued": 8,
                "native_profile_exported": 7,
                "native_profile_dropped": int(dropped),
                "private_policy": "must-stay-private",
            }

    observer = Observer()
    with pytest.raises(ValueError, match="OWNED_PROFILE_CAPTURE_INCOMPLETE") as error:
        module.require_profile_capture(observer, 8, timeout_seconds=10)
    assert observer.calls == (1 if dropped else 2)
    assert "must-stay-private" not in str(error.value)
    assert "native_profile_exported=7" in str(error.value)
