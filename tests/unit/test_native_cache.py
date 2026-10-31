"""Pinned native responses, closed transports, no retries, bounded cache privacy."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI, OpenAI
from openai.types.responses import Response
from pydantic import ValidationError

from cutback.core.exact_cache import EXACT_CACHE_CONTRACT
from cutback.native_cache import NativeCacheScope, NativeResponsesCache

REQUEST = {
    "model": "gpt-4.1",
    "input": "PRIVATE_OWNED_CACHE_INPUT",
    "instructions": "PRIVATE_OWNED_CACHE_INSTRUCTION",
    "max_output_tokens": 64,
    "store": False,
}


def native_body(*, status="completed", usage=True):
    result = {
        "id": "resp_owned_cache",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4.1",
        "status": status,
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "tools": [],
        "output": [
            {
                "id": "msg_owned_cache",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "PRIVATE_OWNED_CACHE_OUTPUT", "annotations": []}
                ],
            }
        ],
    }
    if usage:
        result["usage"] = {
            "input_tokens": 100,
            "output_tokens": 2,
            "total_tokens": 102,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    return result


def scope(**changes):
    return NativeCacheScope(
        endpoint="http://127.0.0.1:8080",
        project_id=uuid4(),
        policy_id=uuid4(),
        token="owned-scoped-key-" + "x" * 32,
        request_hmac_key=b"owned-hmac-key-" + b"x" * 32,
        workflow="route",
        environment="staging",
        prompt_revision="owned-prompt-v1",
        freshness_ref="owned-catalog-v1",
        ttl_seconds=60,
        content_retention_authorized=True,
        immutable_or_safely_invalidated=True,
        **changes,
    )


class CacheTransport:
    def __init__(self, mode="normal"):
        self.mode = mode
        self.calls = []
        self.values = {}
        self.protocol = EXACT_CACHE_CONTRACT

    def __call__(self, request):
        body = json.loads(request.content)
        self.calls.append((request.url.path, body))
        key = body["key_sha256"]
        if request.url.path.endswith("lookup"):
            if self.mode == "denied":
                return httpx.Response(403, json={"secret": "PRIVATE_CACHE_ERROR"})
            if self.mode == "outage":
                raise httpx.ConnectError("PRIVATE_CACHE_NETWORK_ERROR", request=request)
            if self.mode == "redirect":
                return httpx.Response(307, headers={"location": "https://hostile.example/"})
            if key not in self.values:
                return httpx.Response(
                    200, json={"contract": self.protocol, "hit": False, "response": None}
                )
            return httpx.Response(
                200,
                json={
                    "contract": self.protocol,
                    "hit": True,
                    "response": self.values[key],
                    "cache_id": str(uuid4()),
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
                },
            )
        self.values[key] = body["response"]
        if self.mode == "put_uncertain":
            raise httpx.ConnectError("PRIVATE_CACHE_WRITE_ERROR", request=request)
        return httpx.Response(201, json={"contract": self.protocol, "id": str(uuid4())})


def clients(handler=None, *, mode="normal", retry=0):
    calls = []

    def provider(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200, json=native_body())

    cache = CacheTransport(mode)
    http = httpx.Client(transport=httpx.MockTransport(cache), trust_env=False)
    native = OpenAI(
        api_key="owned-synthetic-only",
        max_retries=retry,
        http_client=httpx.Client(transport=httpx.MockTransport(provider), trust_env=False),
    )
    return native, NativeResponsesCache(scope(), http_client=http), cache, calls, http


def test_dispatch_timeout_is_bounded_but_does_not_change_the_semantic_cache_key():
    native, cache, _, calls, http = clients()
    try:
        first = cache.create(
            native, dispatch_timeout_seconds=1, **{**REQUEST, "instructions": None}
        )
        second = cache.create(
            native, dispatch_timeout_seconds=2, **{**REQUEST, "instructions": None}
        )
        assert first.receipt.native_sdk_calls == 1 and second.receipt.native_sdk_calls == 0
        assert len(calls) == 1
        with pytest.raises(ValueError, match="BOUNDED_DISPATCH_TIMEOUT"):
            cache.create(native, dispatch_timeout_seconds=float("nan"), **REQUEST)
        assert len(calls) == 1
    finally:
        native.close()
        http.close()


def test_native_miss_then_hit_preserves_sdk_response_and_never_rebills_cached_usage(caplog):
    native, cache, transport, calls, http = clients()
    caplog.set_level(logging.DEBUG)
    observed = []
    create = native.responses.create

    def capture(**request):
        response = create(**request)
        observed.append(response)
        return response

    native.responses.create = capture
    try:
        first = cache.create(native, **REQUEST)
        second = cache.create(native, **REQUEST)
        assert first.response is observed[0] and isinstance(second.response, Response)
        assert (
            first.response.output_text
            == second.response.output_text
            == "PRIVATE_OWNED_CACHE_OUTPUT"
        )
        assert first.receipt.native_sdk_calls == 1 and first.receipt.cache_write == "created"
        assert second.receipt.native_sdk_calls == 0
        assert second.receipt.usage_origin == "historical_cached_response"
        assert second.receipt.invoice_verified_cost_usd is None
        assert second.response.usage.input_tokens == first.response.usage.input_tokens == 100
        assert len(calls) == 1
        assert json.loads(calls[0].content) == REQUEST
        lookup = transport.calls[0][1]
        assert "PRIVATE_OWNED_CACHE_INPUT" not in json.dumps(lookup)
        assert "PRIVATE_OWNED_CACHE_INSTRUCTION" not in json.dumps(lookup)
        assert len(lookup["key_sha256"]) == 64
        assert "PRIVATE_OWNED_CACHE_OUTPUT" not in repr(first)
        assert "PRIVATE_" not in caplog.text and "owned-synthetic-only" not in caplog.text
        assert cache.scope.token.get_secret_value() not in caplog.text
    finally:
        native.close()
        http.close()


@pytest.mark.parametrize("mode", ["denied", "outage", "redirect"])
def test_unavailable_or_unauthorized_cache_never_dispatches_a_native_request(mode):
    native, cache, transport, calls, http = clients(mode=mode)
    try:
        with pytest.raises(ValueError):
            cache.create(native, **REQUEST)
        assert not calls and len(transport.calls) == 1
    finally:
        native.close()
        http.close()


def test_uncertain_cache_write_returns_original_native_response_without_provider_retry():
    native, cache, _, calls, http = clients(mode="put_uncertain")
    try:
        first = cache.create(native, **REQUEST)
        assert first.receipt.cache_write == "uncertain"
        second = cache.create(native, **REQUEST)
        assert second.receipt.origin == "encrypted_cache" and len(calls) == 1
    finally:
        native.close()
        http.close()


def test_provider_outage_preserves_native_exception_and_performs_no_retry_or_cache_write():
    native, cache, transport, calls, http = clients(
        lambda request: httpx.Response(503, json={"error": {"message": "PRIVATE_NATIVE_ERROR"}})
    )
    try:
        with pytest.raises(APIStatusError):
            cache.create(native, **REQUEST)
        assert len(calls) == 1 and len(transport.calls) == 1
    finally:
        native.close()
        http.close()


@pytest.mark.parametrize(
    "change",
    [
        {"stream": True},
        {"store": True},
        {"tools": []},
        {"input": [{"role": "user", "content": "owned"}]},
        {"max_output_tokens": True},
    ],
)
def test_unqualified_stream_stateful_or_unbounded_inputs_stop_before_all_io(change):
    native, cache, transport, calls, http = clients()
    try:
        with pytest.raises(ValueError):
            cache.create(native, **{**REQUEST, **change})
        assert not calls and not transport.calls
    finally:
        native.close()
        http.close()


def test_retrying_client_is_refused_before_all_io():
    native, cache, transport, calls, http = clients(retry=1)
    try:
        with pytest.raises(ValueError, match="NO_RETRY"):
            cache.create(native, **REQUEST)
        assert not calls and not transport.calls
    finally:
        native.close()
        http.close()


def test_async_client_is_explicitly_unsupported_without_opening_a_transport():
    native, cache, transport, calls, http = clients()
    asynchronous = AsyncOpenAI(api_key="owned-synthetic-only", max_retries=0)
    try:
        with pytest.raises(ValueError, match="QUALIFIED_SYNC"):
            cache.create(asynchronous, **REQUEST)
        assert not calls and not transport.calls
    finally:
        asyncio.run(asynchronous.close())
        native.close()
        http.close()


@pytest.mark.parametrize("usage", [False, True])
def test_cached_missing_usage_remains_unknown(usage):
    native, cache, _, calls, http = clients(
        lambda request: httpx.Response(200, json=native_body(usage=usage))
    )
    try:
        cache.create(native, **REQUEST)
        hit = cache.create(native, **REQUEST)
        assert hit.receipt.origin == "encrypted_cache" and len(calls) == 1
        assert (hit.response.usage is None) is (not usage)
        assert hit.receipt.invoice_verified_cost_usd is None
    finally:
        native.close()
        http.close()


def test_cached_request_binding_cannot_be_swapped_or_replayed_as_another_native_call():
    native, cache, transport, calls, http = clients()
    try:
        cache.create(native, **REQUEST)
        for key in transport.values:
            stored = json.loads(transport.values[key])
            stored["request_hmac"] = "a" * 64
            transport.values[key] = json.dumps(stored)
        with pytest.raises(ValueError, match="STORED_RESPONSE_INVALID"):
            cache.create(native, **REQUEST)
        assert len(calls) == 1
    finally:
        native.close()
        http.close()


def test_scope_errors_and_representations_do_not_echo_secret_material():
    value = scope()
    assert value.token.get_secret_value() not in repr(value)
    assert value.request_hmac_key.get_secret_value().decode() not in repr(value)
    with pytest.raises(ValidationError) as failure:
        NativeCacheScope.model_validate({**value.model_dump(), "ttl_seconds": 0})
    assert value.token.get_secret_value() not in str(failure.value)
    assert value.request_hmac_key.get_secret_value().decode() not in str(failure.value)


def test_new_sdk_usage_fields_omitted_by_the_server_stay_unknown_after_replay():
    native, cache, _, calls, http = clients()
    try:
        fresh = cache.create(native, **REQUEST)
        cached = cache.create(native, **REQUEST)
        assert cached.receipt.origin == "encrypted_cache" and len(calls) == 1
        assert (
            getattr(fresh.response.usage.input_tokens_details, "cache_write_tokens", None) is None
        )
        assert (
            getattr(cached.response.usage.input_tokens_details, "cache_write_tokens", None) is None
        )
    finally:
        native.close()
        http.close()


def test_incomplete_native_response_is_returned_but_never_cached_or_retried():
    native, cache, transport, calls, http = clients(
        lambda request: httpx.Response(200, json=native_body(status="incomplete"))
    )
    try:
        result = cache.create(native, **REQUEST)
        assert result.response.status == "incomplete"
        assert result.receipt.cache_write == "not_eligible"
        assert len(calls) == 1 and len(transport.calls) == 1
    finally:
        native.close()
        http.close()


def test_invalid_usage_types_in_a_cached_payload_do_not_dispatch_a_replacement_request():
    native, cache, transport, calls, http = clients()
    try:
        cache.create(native, **REQUEST)
        for key in transport.values:
            saved = json.loads(transport.values[key])
            saved["response"]["usage"]["input_tokens"] = "PRIVATE_INVALID_NATIVE_USAGE"
            transport.values[key] = json.dumps(saved)
        with pytest.raises(ValueError, match="STORED_RESPONSE_INVALID") as failure:
            cache.create(native, **REQUEST)
        assert "PRIVATE_INVALID_NATIVE_USAGE" not in str(failure.value)
        assert len(calls) == 1
    finally:
        native.close()
        http.close()


@pytest.mark.parametrize("changed", ["api_key", "organization", "project"])
def test_native_provider_account_changes_do_not_reuse_another_accounts_response(changed):
    native, cache, transport, calls, http = clients()
    try:
        cache.create(native, **REQUEST)
        setattr(native, changed, "another-owned-provider-context")
        result = cache.create(native, **REQUEST)
        assert result.receipt.origin == "native_provider" and len(calls) == 2
        lookup_bodies = [body for path, body in transport.calls if path.endswith("lookup")]
        assert lookup_bodies[0]["key_sha256"] != lookup_bodies[1]["key_sha256"]
        assert "another-owned-provider-context" not in json.dumps(lookup_bodies)
    finally:
        native.close()
        http.close()


@pytest.mark.parametrize(
    "field", ["content_retention_authorized", "immutable_or_safely_invalidated"]
)
@pytest.mark.parametrize("value", [1, "true", False, None])
def test_cache_authority_cannot_be_inferred_from_truthy_values(field, value):
    original = scope()
    with pytest.raises(ValidationError):
        NativeCacheScope.model_validate({**original.model_dump(), field: value})
