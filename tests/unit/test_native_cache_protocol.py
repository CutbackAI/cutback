"""A matching collector contract is required before native dispatch or cache acceptance."""

import json

import httpx
import pytest
from test_native_cache import REQUEST, clients, native_body


@pytest.mark.parametrize("protocol", [None, "older-contract", True, ""])
def test_missing_or_mismatched_collector_contract_stops_before_native_dispatch(protocol):
    native, cache, transport, calls, http = clients()
    transport.protocol = protocol
    try:
        with pytest.raises(ValueError, match="COLLECTOR_CONTRACT"):
            cache.create(native, **REQUEST)
        assert not calls
    finally:
        native.close()
        http.close()


def test_a_changed_write_contract_after_native_success_never_causes_a_second_dispatch():
    transport = None

    def provider(request):
        transport.protocol = "older-contract"
        return httpx.Response(200, json=native_body())

    native, cache, transport, calls, http = clients(provider)
    try:
        result = cache.create(native, **REQUEST)
        assert result.receipt.cache_write == "uncertain" and len(calls) == 1
        with pytest.raises(ValueError, match="COLLECTOR_CONTRACT"):
            cache.create(native, **REQUEST)
        assert len(calls) == 1
    finally:
        native.close()
        http.close()


def test_legacy_extra_forbidden_request_contract_never_receives_a_response_payload():
    native, cache, _, calls, original_http = clients()
    requests = []

    def legacy_server(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["contract"] == "native-exact-cache-v1" and "response" not in body
        return httpx.Response(422, json={"detail": "extra_field_forbidden"})

    legacy_http = httpx.Client(transport=httpx.MockTransport(legacy_server), trust_env=False)
    cache.http = legacy_http
    try:
        with pytest.raises(ValueError, match="LOOKUP_UNAVAILABLE_NO_PROVIDER_REQUEST"):
            cache.create(native, **REQUEST)
        assert len(requests) == 1 and not calls
    finally:
        native.close()
        original_http.close()
        legacy_http.close()


def test_unsafe_model_metadata_is_refused_before_any_request():
    native, cache, transport, calls, http = clients()
    try:
        with pytest.raises(ValueError, match="MODEL_IDENTIFIER_REQUIRES_REVIEW"):
            cache.create(native, **{**REQUEST, "model": "sk-synthetic-private-label"})
        assert not calls and not transport.calls
    finally:
        native.close()
        http.close()
