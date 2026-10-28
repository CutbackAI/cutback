import json
import threading
from datetime import UTC, datetime
from uuid import uuid4

import httpx

from cutback.core.task_outcomes import TaskOutcomeInput
from cutback.instrumentation.task_outcomes import TaskOutcomeExporter


def task():
    return TaskOutcomeInput(
        task_id=uuid4(),
        result_id=uuid4(),
        environment="staging",
        trace_id=uuid4().hex,
        workflow="synthetic.task",
        service="synthetic-test",
        occurred_at=datetime.now(UTC),
        outcome="unknown",
        source="owned-app",
        source_version="1",
        basis="business_event",
    )


def test_explicit_export_is_metadata_only_bounded_and_idempotent_identity(caplog):
    sent = []
    token = "synthetic-private-ingest-key-" + uuid4().hex

    def receive(request):
        assert request.headers["Authorization"] == "Bearer " + token
        body = json.loads(request.content)
        sent.extend(body["items"])
        return httpx.Response(200, json={"accepted": len(body["items"]), "duplicates": 0})

    exporter = TaskOutcomeExporter(
        endpoint="http://127.0.0.1:8766/v1/ingest/task-outcomes",
        token=token,
        transport=httpx.MockTransport(receive),
    )
    item = task()
    assert exporter.emit(item) and exporter.emit(item)
    assert exporter.flush(2)
    assert 1 <= len(sent) <= 2 and {r["result_id"] for r in sent} == {str(item.result_id)}
    assert len(sent) + exporter.health().get("coalesced", 0) == 2
    assert all(r["outcome"] == "unknown" for r in sent)
    assert exporter.health()["exported"] == 2
    assert not exporter.emit(item.model_copy(update={"source": "raw content invalid identifier"}))
    assert exporter.close(2) and not exporter.emit(item)
    assert token not in caplog.text and str(item.result_id) not in caplog.text


def test_queue_saturation_and_outage_drop_without_retry_or_application_block():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def outage(request):
        calls.append(request.url)
        entered.set()
        assert release.wait(2)
        raise httpx.ReadTimeout("synthetic confidential error")

    exporter = TaskOutcomeExporter(
        endpoint="http://127.0.0.1:8766/v1/ingest/task-outcomes",
        token="x" * 40,
        capacity=1,
        batch_size=1,
        transport=httpx.MockTransport(outage),
    )
    try:
        assert exporter.emit(task()) and entered.wait(2)
        assert exporter.emit(task())
        assert not exporter.emit(task())
        assert not exporter.flush(0.01)
    finally:
        release.set()
        assert exporter.close(2)
    assert len(calls) == 2
    assert exporter.health()["dropped"] == 3
    assert exporter.health()["export_failed"] == 2


def test_redirect_never_forwards_scoped_credentials():
    calls = []

    def redirect(request):
        calls.append(request.url.host)
        return httpx.Response(307, headers={"Location": "https://untrusted.example/collect"})

    exporter = TaskOutcomeExporter(
        endpoint="http://127.0.0.1:8766/v1/ingest/task-outcomes",
        token="x" * 40,
        transport=httpx.MockTransport(redirect),
    )
    assert exporter.emit(task()) and exporter.close(2)
    assert calls == ["127.0.0.1"] and exporter.health()["dropped"] == 1
