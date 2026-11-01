"""Actual native SDK responses and metadata-only, bounded closed transports."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from test_native_cache import native_body
from test_source_improvement_policy import policy

import cutback.instrumentation as instrumentation
from cutback.instrumentation.policy_activity import PolicyActivityObserver
from cutback.native_cache import NativeCacheReceipt, NativeCacheResult
from cutback.source_policy import responses_create, responses_create_async


class Attribution:
    def resolve(self):
        return {
            "workflow_id": "owned-ticket-priority",
            "call_site_id": "site:" + "a" * 24,
            "source_commit": "b" * 40,
        }


@pytest.fixture
def observed(monkeypatch):
    original = httpx.Client
    exported = []

    def intake(request):
        body = json.loads(request.content)
        exported.append((request.url.path, body))
        return httpx.Response(200, json={"accepted": len(body.get("events", [])), "duplicate": 0})

    monkeypatch.setattr(
        "cutback.instrumentation.policy_activity.httpx",
        SimpleNamespace(Client=lambda **kw: original(transport=httpx.MockTransport(intake), **kw)),
    )
    observer = PolicyActivityObserver(
        service_name="owned-support",
        environment="staging",
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        token="owned-" + "x" * 40,
        attribution=Attribution(),
    )
    monkeypatch.setattr(instrumentation, "_active", SimpleNamespace(policy_runtime=observer))
    yield observer, exported, original
    assert observer.close(2)


def events(observed):
    assert observed[0].flush(2)
    return [
        event
        for path, body in observed[1]
        if path.endswith("policy-runtime")
        for event in body["events"]
    ]


def test_native_response_retry_policy_and_inactive_cache_are_preserved(observed):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(
            503 if len(calls) == 1 else 200,
            json={"error": {"message": "synthetic"}} if len(calls) == 1 else native_body(),
        )

    with observed[2](transport=httpx.MockTransport(provider)) as transport:
        from openai import OpenAI

        with OpenAI(
            api_key="synthetic-private-key", max_retries=1, http_client=transport
        ) as client:
            result = responses_create(client, "OWNED_PRIVATE_ACTIVITY_INPUT", policy(cached=True))
            from openai.types.responses import Response

            assert type(result) is Response and client.max_retries == 1 and len(calls) == 2
    (event,) = events(observed)
    assert event["cache_configured"] and not event["cache_requested"]
    assert event["state"] == "inactive" and event["native_sdk_invocations"] == 1
    assert event["cache_operations"] == 0 and event["invoice_verified_cost_usd"] is None
    assert "OWNED_PRIVATE_ACTIVITY_INPUT" not in json.dumps(observed[1])
    assert "synthetic-private-key" not in json.dumps(observed[1])


@pytest.mark.parametrize(
    "hit,write",
    [(True, "not_attempted"), (False, "created"), (False, "uncertain"), (False, "not_eligible")],
)
def test_cache_receipt_metadata_never_reprices_or_reads_historical_response(
    observed, monkeypatch, hit, write
):
    response = object()
    receipt = NativeCacheReceipt(
        "encrypted_cache" if hit else "native_provider",
        int(not hit),
        write,
        1,
        "historical_cached_response" if hit else "native_response",
    )

    def cached(*args, **kw):
        return NativeCacheResult(response, receipt)

    monkeypatch.setattr("cutback.native_cache.responses_create_from_scope_file", cached)
    from openai import OpenAI

    with OpenAI(
        api_key="synthetic-key",
        http_client=observed[2](
            transport=httpx.MockTransport(lambda req: pytest.fail("unexpected provider call"))
        ),
    ) as client:
        assert (
            responses_create(
                client,
                "OWNED_PRIVATE_ACTIVITY_INPUT",
                policy(cached=True),
                scope_file="OWNED_PRIVATE_SCOPE_PATH",
            )
            is response
        )
    (event,) = events(observed)
    assert event["state"] == ("hit" if hit else "native_miss")
    assert event["cache_operations"] == (1 if hit or write == "not_eligible" else 2)
    assert event["usage_origin"] == receipt.usage_origin and event["cache_write"] == write
    assert "OWNED_PRIVATE_SCOPE_PATH" not in json.dumps(observed[1])


def test_cache_outage_stays_unknown_without_a_replayed_native_request(observed, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValueError("OWNED_PRIVATE_EXCEPTION_CONTENT")

    monkeypatch.setattr("cutback.native_cache.responses_create_from_scope_file", unavailable)
    from openai import OpenAI

    with OpenAI(
        api_key="synthetic",
        http_client=observed[2](
            transport=httpx.MockTransport(lambda req: pytest.fail("unexpected provider replay"))
        ),
    ) as client:
        with pytest.raises(ValueError, match="OWNED_PRIVATE_EXCEPTION_CONTENT"):
            responses_create(client, "owned", policy(cached=True), scope_file="private")
    (event,) = events(observed)
    assert event["state"] == "unknown" and event["completion"] == "failed"
    assert event["native_sdk_invocations"] is event["cache_operations"] is None
    assert "OWNED_PRIVATE_EXCEPTION_CONTENT" not in json.dumps(observed[1])


def test_observer_errors_and_missing_attribution_never_change_native_results(observed, monkeypatch):
    original = observed[0].finish

    def broken(*args, **kw):
        raise RuntimeError("owned-telemetry-error")

    monkeypatch.setattr(observed[0], "finish", broken)
    from openai import OpenAI

    with OpenAI(
        api_key="synthetic",
        http_client=observed[2](
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=native_body()))
        ),
    ) as client:
        assert responses_create(client, "owned", policy()).status == "completed"
        monkeypatch.setattr(observed[0], "finish", original)
        monkeypatch.setattr(observed[0].attribution, "resolve", lambda: {})
        assert responses_create(client, "owned", policy()).status == "completed"
    assert events(observed) == []
    assert observed[0].health()["policy_runtime_normalization_failed"] == 1
    assert observed[0].health()["policy_runtime_unattributed"] == 1


def test_async_native_cancellation_is_recorded_without_cache_or_fallback(observed):
    from openai import AsyncOpenAI

    async def exercise():
        dispatched = asyncio.Event()

        async def provider(request):
            dispatched.set()
            await asyncio.Event().wait()

        async with AsyncOpenAI(
            api_key="synthetic",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
        ) as client:
            task = asyncio.create_task(
                responses_create_async(client, "OWNED_PRIVATE_CANCEL_INPUT", policy())
            )
            await asyncio.wait_for(dispatched.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not client.is_closed()

    asyncio.run(exercise())
    (event,) = events(observed)
    assert event["completion"] == "cancelled" and event["native_sdk_invocations"] == 1
    assert event["usage_origin"] is None and event["cache_operations"] == 0


@pytest.mark.parametrize("status", [401, 409, 429, 503])
def test_background_rejection_has_no_retries_redirects_or_private_diagnostics(monkeypatch, status):
    original = httpx.Client
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            status,
            headers={"Location": "https://example.invalid/steal"},
            text="OWNED_PRIVATE_ERROR",
        )

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handler), **kw)
    )
    observer = PolicyActivityObserver(
        service_name="owned",
        environment="staging",
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        token="x" * 48,
        attribution=Attribution(),
    )
    try:
        token = observer.begin(
            workflow="owned-ticket-priority",
            policy_sha256="c" * 64,
            cache_configured=False,
            cache_requested=False,
            cache_operation_upper_usd=None,
            cache_cost_basis_ref=None,
        )
        observer.finish(token, completion="completed")
        assert observer.flush(2)
        assert calls == ["/v1/ingest/policy-runtime", "/v1/ingest/health"]
        health = observer.health()
        assert health["policy_runtime_dropped"] == 1 and health["policy_runtime_exported"] == 0
        assert "OWNED_PRIVATE_ERROR" not in str(health)
    finally:
        observer.close(2)


def test_bounded_queue_and_shutdown_drop_metadata_without_blocking_the_caller(monkeypatch):
    original = httpx.Client
    dispatched, release = threading.Event(), threading.Event()

    def handler(request):
        if request.url.path.endswith("policy-runtime"):
            dispatched.set()
            assert release.wait(3)
            return httpx.Response(200, json={"accepted": 1, "duplicate": 0})
        return httpx.Response(200, json={"status": "recorded"})

    monkeypatch.setattr(
        "cutback.instrumentation.policy_activity.httpx",
        SimpleNamespace(Client=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)),
    )
    observer = PolicyActivityObserver(
        service_name="owned",
        environment="staging",
        endpoint="http://127.0.0.1:8080/v1/ingest/events",
        project_id=str(uuid4()),
        token="x" * 48,
        attribution=Attribution(),
        capacity=1,
        batch_size=1,
    )

    def token():
        return observer.begin(
            workflow="policy-workflow-differs-from-scanner-entrypoint",
            policy_sha256="c" * 64,
            cache_configured=False,
            cache_requested=False,
            cache_operation_upper_usd=None,
            cache_cost_basis_ref=None,
        )

    try:
        observer.finish(token(), completion="completed")
        assert dispatched.wait(2)
        started = time.monotonic()
        for _ in range(20):
            observer.finish(token(), completion="completed")
        assert time.monotonic() - started < 1
        assert observer.health()["policy_runtime_queue_depth"] == 1
        assert observer.health()["policy_runtime_dropped"] == 19
        assert not observer.flush(0.001)
        release.set()
        assert observer.close(2)
        observer.finish(token(), completion="completed")
        assert observer.health()["policy_runtime_dropped"] == 20
        assert observer.health()["policy_runtime_exported"] == 2
    finally:
        release.set()
        observer.close(2)


def test_explicit_activation_requires_a_source_map_and_restores_native_hooks_on_refusal(
    monkeypatch,
):
    from openai.resources.responses import Responses

    original = Responses.create
    monkeypatch.setattr(instrumentation, "_active", None)
    monkeypatch.setenv("CUTBACK_PROJECT_TOKEN", "x" * 48)
    with pytest.raises(ValueError, match="CURRENT_SOURCE_MAP_REQUIRED"):
        instrumentation.init(
            service_name="owned",
            environment="staging",
            enabled=True,
            endpoint="http://127.0.0.1:8080/v1/ingest/events",
            project_id=str(uuid4()),
            providers=("openai",),
            observe_policy_runtime=True,
        )
    assert Responses.create is original
