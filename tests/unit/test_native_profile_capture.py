"""Opt-in profiling must preserve native sync/async/stream/retry/outage behavior."""

import asyncio
import json
import os
import queue
import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import openai
import pytest
from unit.test_instrumentation import observer as observer
from unit.test_native_cache import REQUEST, native_body

from cutback.core.native_request_profile import NativeProfileObservation, summarize_native_profile
from cutback.evals.datasets import CapturePolicy
from cutback.instrumentation import workflow
from cutback.instrumentation.capture import CaptureExporter


def policy(**changes):
    return CapturePolicy(
        enabled=True,
        approval_ref="owned-profile-test",
        rights_basis="synthetic",
        environments=["staging"],
        workflows=["classify"],
        allowed_providers=["openai"],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        sample_rate=1,
        native_request_profile={
            "freshness_ref": "owned-immutable",
            "prospective_ttl_seconds": 300,
            "fingerprint_capture_authorized": True,
            "immutable_or_safely_invalidated": True,
        },
        **changes,
    )


def capture(owner):
    exporter = CaptureExporter.__new__(CaptureExporter)
    exporter.project_id, exporter.policy_id = uuid4(), uuid4()
    exporter.policy = policy()
    exporter.closed = threading.Event()
    exporter._profile_lock = threading.Lock()
    exporter._profile_key = os.urandom(32)
    exporter._profile_key_id = uuid4()
    exporter._profile_clock_domain_id = uuid4()
    exporter._profile_identity = None
    exporter.queue = queue.Queue(16)
    exporter.counters = {"captured_queued": 0, "captured_dropped": 0}
    owner.capture = exporter
    return exporter


def drain(exporter):
    values = []
    while not exporter.queue.empty():
        values.append(exporter.queue.get_nowait())
        exporter.queue.task_done()
    return values


@pytest.mark.parametrize("kind", ["plain", "async", "stream", "retries", "outage", "deny"])
def test_profiling_preserves_native_objects_calls_and_privacy(observer, monkeypatch, kind):
    exporter = capture(observer)
    calls = []

    def respond(request):
        calls.append(request)
        if kind == "outage":
            raise httpx.ConnectError("OWNED_PROVIDER_FAILURE", request=request)
        if kind == "retries" and len(calls) == 1:
            return httpx.Response(
                429, headers={"retry-after-ms": "1"}, json={"error": {"message": "owned"}}
            )
        if kind == "stream":
            chunk = {"type": "response.completed", "sequence_number": 0, "response": native_body()}
            return httpx.Response(
                200,
                text="data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=native_body())

    transport = httpx.MockTransport(respond)
    if kind == "deny":
        monkeypatch.setenv("CUTBACK_CAPTURE_DENY", "true")

    async def run_async():
        async with openai.AsyncOpenAI(
            api_key="owned-profile-provider",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport, trust_env=False),
        ) as client:
            return await client.responses.create(**REQUEST)

    with workflow("classify"):
        if kind == "async":
            result = asyncio.run(run_async())
        else:
            with openai.OpenAI(
                api_key="owned-profile-provider",
                max_retries=1 if kind == "retries" else 0,
                http_client=httpx.Client(transport=transport, trust_env=False),
            ) as client:
                if kind == "outage":
                    with pytest.raises(openai.APIConnectionError):
                        client.responses.create(**REQUEST)
                    result = None
                else:
                    result = client.responses.create(**{**REQUEST, "stream": kind == "stream"})
                    if kind == "stream":
                        assert isinstance(result, openai.Stream)
                        with result:
                            assert len(list(result)) == 1
    assert len(calls) == (2 if kind == "retries" else 1)
    events = observer.exporter.events
    assert len(events) == len(calls)
    assert all("PRIVATE_OWNED" not in event.model_dump_json() for event in events)
    assert all("request_hmac_sha256" not in event.model_dump_json() for event in events)
    samples = drain(exporter)
    if kind == "plain":
        assert len(samples) == 1 and samples[0].native_request_profile.response_cache_eligible
    else:
        assert all(sample.native_request_profile is None for sample in samples)
    if kind not in {"stream", "outage"}:
        assert isinstance(result, openai.types.responses.Response)
        assert result.output_text == "PRIVATE_OWNED_CACHE_OUTPUT"
    if kind == "outage":
        assert events[0].status == "error" and not samples
    if kind in {"stream", "deny"}:
        assert not samples and exporter._profile_identity is None


@pytest.mark.parametrize("key", ["", "not-base64", "YQ==", "x" * 513])
def test_invalid_private_profile_key_never_breaks_ordinary_capture(monkeypatch, key):
    approved = policy()
    monkeypatch.setenv("CUTBACK_NATIVE_REQUEST_PROFILE_HMAC_KEY", key)
    monkeypatch.setattr(
        CaptureExporter,
        "client",
        lambda self: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"policy": approved.model_dump(mode="json")},
                )
            ),
            trust_env=False,
        ),
    )
    exporter = CaptureExporter("http://127.0.0.1:8080/v1/ingest/events", uuid4(), "owned", uuid4())
    try:
        assert exporter._profile_key is None
        assert exporter.counters["native_profile_unconfigured"] == 1
    finally:
        exporter.close()
        exporter.thread.join(1)
    assert not exporter.thread.is_alive()


def test_busy_profile_derivation_never_blocks_and_close_cannot_restore_secret(
    observer, monkeypatch
):
    exporter = capture(observer)
    entered, release = threading.Event(), threading.Event()

    def derive(*args):
        entered.set()
        assert release.wait(2)
        exporter._profile_key = b"owned-in-flight-key" * 2
        return None

    monkeypatch.setattr(exporter, "_request_profile_for_locked", derive)
    thread = threading.Thread(target=lambda: exporter.request_profile_for({}, {}, None))
    thread.start()
    try:
        assert entered.wait(2)
        assert exporter.request_profile_for({}, {}, None) is None
        assert exporter.counters["native_profile_busy"] == 1
        exporter.close(0)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and exporter._profile_key is None


def test_legacy_policy_and_sample_wire_format_stays_unchanged():
    value = policy().model_dump(mode="json")
    value.pop("native_request_profile")
    old = CapturePolicy.model_validate(value)
    assert old.model_dump(mode="json") == value


def test_fast_serial_calls_use_one_high_resolution_clock_despite_coarse_platform_monotonic(
    observer,
    monkeypatch,
):
    import time

    exporter = capture(observer)
    # Python 3.11 on Windows exposes GetTickCount64 (15.625ms) separately
    # from QueryPerformanceCounter. Simulate the coarse clock on every host.
    monkeypatch.setattr(time, "monotonic_ns", lambda: 0)
    with (
        openai.OpenAI(
            api_key="owned-profile-clock",
            max_retries=0,
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=native_body()),
                ),
                trust_env=False,
            ),
        ) as client,
        workflow("classify"),
    ):
        for _ in range(4):
            client.responses.create(**REQUEST)
    samples = drain(exporter)
    assert len(samples) == 4
    rows = [
        NativeProfileObservation(
            event_id=event.event_id,
            occurred_at=event.occurred_at,
            duration_ms=event.duration_ms,
            profile=sample.native_request_profile,
            provider_modeled_cost_usd="0.01",
        )
        for event, sample in zip(observer.exporter.events, samples, strict=True)
    ]
    result = summarize_native_profile(
        rows,
        exporter.policy.native_request_profile,
        capture_policy_sha256=rows[0].profile.capture_policy_sha256,
        metadata_attempts=4,
        sample_rate=1,
    )
    assert result["modeled_potential_hits"] == 3
    assert result["overlapping_cold_misses"] == 0
