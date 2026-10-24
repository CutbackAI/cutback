"""Execute real framework/native SDK examples with all provider HTTP intercepted."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient


def module(name):
    root = Path(__file__).resolve().parents[2]
    # Examples are source artifacts, not mandatory installed Cutback modules.
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return __import__("examples.runnable." + name, fromlist=[name])


def test_native_sync_async_stream_and_bounded_worker_examples():
    example = module("sync_async_streams")
    example.synchronous()
    asyncio.run(example.asynchronous())
    module("worker").main()


def test_fastapi_example_is_bounded_and_uses_both_native_clients():
    with TestClient(module("fastapi_app").app) as client:
        response = client.post("/classify", json={"text": "Owned synthetic example"})
        assert response.json() == {
            "synthetic": True,
            "openai": "synthetic answer",
            "anthropic": "synthetic answer",
        }
        assert client.post("/classify", json={"text": "x" * 1025}).status_code == 422


def test_django_and_lambda_examples_preserve_application_behavior_and_bound_flush():
    example = module("django_demo")
    example.configure()
    from django.test import Client

    assert Client().get("/synthetic").json() == {"synthetic": True, "answer": "synthetic answer"}
    function = module("lambda_handler")

    class Context:
        def get_remaining_time_in_millis(self):
            return 75

    class Observer:
        def __init__(self):
            self.deadlines = []

        def flush(self, deadline):
            self.deadlines.append(deadline)
            raise TimeoutError("synthetic export outage")

    observer = Observer()
    result = function.bounded_invocation(function.application_handler, observer)({}, Context())
    assert result == {"synthetic": True, "answer": "synthetic answer"}
    assert observer.deadlines == [0.025]
