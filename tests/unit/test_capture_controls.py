from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx

from cutback.evals.datasets import CapturePolicy
from cutback.instrumentation.capture import CaptureExporter, bounded_content


def test_capture_kill_switch_drains_queue_and_never_sends_content(monkeypatch):
    policy = CapturePolicy(
        enabled=True,
        approval_ref="synthetic",
        rights_basis="synthetic",
        environments=["staging"],
        workflows=["classify"],
        allowed_providers=["openai"],
        expires_at=datetime.now(UTC) + timedelta(days=1),
        sample_rate=1,
    )
    original = httpx.Client
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json={"policy": policy.model_dump(mode="json")})

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    exporter = CaptureExporter(
        "http://127.0.0.1:8080/v1/ingest/events", uuid4(), "synthetic", uuid4()
    )
    event = SimpleNamespace(
        event_id=uuid4(), environment="staging", workflow_id="classify", provider="openai"
    )
    try:
        monkeypatch.setenv("CUTBACK_CAPTURE_DENY", "true")
        exporter.emit(event, "PRIVATE", SimpleNamespace(output_text="PRIVATE"))
        assert exporter.flush(1)
        assert exporter.thread.is_alive()
        assert exporter.counters["captured_dropped"] == 1
        assert len(requests) == 1  # Initialization policy read only.
    finally:
        exporter.close()
        exporter.thread.join(1)


def test_capture_output_deny_includes_anthropic_fallback():
    exporter = CaptureExporter.__new__(CaptureExporter)
    exporter.closed = SimpleNamespace(is_set=lambda: False)
    exporter.policy = SimpleNamespace(sample_rate=1, never_capture_fields=["output"])
    exporter.policy_id = uuid4()
    exporter.counters = {"captured_queued": 0}
    samples = []
    exporter.queue = SimpleNamespace(put_nowait=samples.append)
    event = SimpleNamespace(
        event_id=uuid4(), environment="staging", workflow_id="classify", provider="anthropic"
    )
    exporter.emit(event, "synthetic", SimpleNamespace(content=[SimpleNamespace(text="PRIVATE")]))
    assert samples[0].reference is None


def test_capture_bounds_nested_input_before_redaction():
    assert bounded_content([{"content": "synthetic"}], 100)
    assert not bounded_content([{"content": "x" * 1000}], 100)
    recursive = []
    recursive.append(recursive)
    assert not bounded_content(recursive, 100)
