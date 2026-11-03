"""Waiting for queued capture does not replay any native request or accept a partial sample."""

import os
import threading
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select
from support_http import owned_collector

from cutback.demo_workload_portfolio import seed_workload_portfolio
from cutback.evals.artifacts import EncryptedFileStore
from cutback.instrumentation.capture import CaptureExporter
from cutback.instrumentation.openai import OpenAIInstrumentation
from cutback.server.db import StoredEvent

pytestmark = pytest.mark.postgres


def test_already_queued_capture_can_finish_after_one_flush_without_replaying_native_calls(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    release, expired = threading.Event(), threading.Event()
    flush_results, sample_posts = [], []
    original_client, original_flush = CaptureExporter.client, OpenAIInstrumentation.flush

    def blocked_client(self):
        client = original_client(self)

        def hold_sample(request):
            if request.method == "POST" and request.url.path.endswith("/samples"):
                assert request.url.host == "127.0.0.1"
                sample_posts.append(True)
                # The barrier spans eight foreground source-bound SDK calls;
                # it is separate from the unchanged request/flush deadlines.
                if not release.wait(60):
                    expired.set()
                    raise AssertionError("OWNED_CAPTURE_GATE_DID_NOT_RELEASE")

        client.event_hooks["request"].append(hold_sample)
        return client

    def observed_flush(self, timeout_seconds=0.3):
        result = original_flush(self, timeout_seconds)
        flush_results.append(result)
        if not result:
            release.set()
        return result

    monkeypatch.setattr(CaptureExporter, "client", blocked_client)
    monkeypatch.setattr(OpenAIInstrumentation, "flush", observed_flush)
    try:
        with (
            owned_collector(api[2]) as endpoint,
            httpx.Client(base_url=endpoint, headers=api[1], timeout=30, trust_env=False) as control,
        ):
            result = seed_workload_portfolio(
                api[2], control, tenant[0], tmp_path, tenant[1]["Authorization"][7:], execute=False
            )
    finally:
        release.set()
    assert flush_results[0] is False and flush_results[-1] is True
    assert not expired.is_set()
    assert result["observed_native_calls"] == 8 and result["target_runs_queued"] == 0
    assert len(sample_posts) == 8
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == UUID(tenant[0]))
            )
            == 8
        )
