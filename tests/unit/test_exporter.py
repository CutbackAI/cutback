import time
from uuid import uuid4

import httpx
import pytest

from cutback.instrumentation.exporter import Exporter


@pytest.mark.parametrize("status", [200, 401, 429, 500])
def test_bounded_export_failure_counters_and_no_redirect(monkeypatch, event, status):
    original = httpx.Client
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"accepted": 1, "duplicate": 0})

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    exporter = Exporter(
        endpoint="http://127.0.0.1:8080/v1/ingest/events", project_id=str(uuid4()), token="a" * 48
    )
    try:
        exporter.emit(event)
        assert exporter.flush(1)
        health = exporter.health()
        assert health["observed"] == 1
        assert health.get("exported", 0) == (1 if status == 200 else 0)
        assert health.get("dropped", 0) == (0 if status == 200 else 1)
        assert len(calls) == 2  # One event batch and one metadata health report; no retries.
        assert calls[1].url.path == "/v1/ingest/health"
    finally:
        exporter.close()


def test_queue_overflow_and_bounded_flush(monkeypatch, event):
    original = httpx.Client

    def handler(request):
        time.sleep(0.1)
        raise httpx.ConnectError("synthetic outage")

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    exporter = Exporter(
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        token="a" * 48,
        capacity=1,
        batch_size=1,
    )
    try:
        start = time.monotonic()
        for _ in range(100):
            exporter.emit(event)
        assert time.monotonic() - start < 0.1
        assert not exporter.flush(0.001)
        assert exporter.health()["dropped"] > 0
        assert exporter.health()["queue_depth"] <= 1
    finally:
        exporter.close(1)
