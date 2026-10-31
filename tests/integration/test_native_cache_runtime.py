"""Explicit private runtime configuration through actual HTTP, PG and pinned native SDK."""

import copy
import json

import httpx
import pytest
from support_http import owned_collector
from test_native_cache_workflow import prepared, request

from cutback.core.exact_cache import ExactCacheExecution
from cutback.core.request_profiles import fingerprint
from cutback.core.workspace import write_private
from cutback.native_cache import responses_create_from_scope_file

pytestmark = pytest.mark.postgres


def runtime_configuration(cache, endpoint):
    contract = ExactCacheExecution(
        freshness_ref=cache.scope.freshness_ref,
        ttl_seconds=cache.scope.ttl_seconds,
        content_retention_authorized=True,
        immutable_or_safely_invalidated=True,
        operation_cost_upper_usd="0.000000001",
        operation_cost_basis_ref="owned-runtime-fixture",
    )
    scope = cache.scope.model_dump(mode="json", exclude={"token", "request_hmac_key"})
    scope.update(
        endpoint=endpoint,
        token=cache.scope.token.get_secret_value(),
        request_hmac_key=cache.scope.request_hmac_key.get_secret_value().decode(),
    )
    configuration = {
        "version": "cutback.native-cache-runtime.v1",
        "contract": contract.model_dump(mode="json"),
        "scope": scope,
    }
    expected = {
        "expected_workflow": cache.scope.workflow,
        "expected_request_revision": cache.scope.prompt_revision,
        "expected_contract_sha256": fingerprint(contract.model_dump(mode="json")),
    }
    return configuration, expected


def test_private_runtime_configuration_preserves_the_borrowed_client_and_never_bypasses_revocation(
    api, tenant, tmp_path, monkeypatch
):
    native, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    native.max_retries = 2
    try:
        with owned_collector(api[2]) as endpoint:
            configuration, expected = runtime_configuration(cache, endpoint)
            path = tmp_path / "private-native-cache.json"
            write_private(path, configuration)
            first = responses_create_from_scope_file(
                native, scope_file=path, **expected, **request()
            )
            hit = responses_create_from_scope_file(native, scope_file=path, **expected, **request())
            assert first.response.output_text == hit.response.output_text
            assert hit.receipt.native_sdk_calls == 0 and len(calls) == 1
            assert native.max_retries == 2 and not native._client.is_closed
            assert json.loads(calls[0].content)["store"] is False
            with httpx.Client(base_url=endpoint, trust_env=False, timeout=10) as owner:
                revoked = owner.delete(
                    f"/v1/projects/{tenant[0]}/capture-policies/{cache.scope.policy_id}",
                    headers=api[1],
                )
                assert revoked.status_code == 200
            with pytest.raises(ValueError, match="CURRENT_POLICY_AND_SCOPED_KEY_REQUIRED"):
                responses_create_from_scope_file(native, scope_file=path, **expected, **request())
            assert len(calls) == 1 and not native._client.is_closed
    finally:
        native.close()


@pytest.mark.parametrize("changed", ["workflow", "revision", "contract", "ttl", "oversized"])
def test_private_runtime_mismatch_stops_before_any_collector_or_native_request(
    api, tenant, tmp_path, monkeypatch, changed
):
    native, cache, calls = prepared(api, tenant, tmp_path, monkeypatch)
    try:
        configuration, expected = runtime_configuration(cache, "http://127.0.0.1:1")
        invalid = copy.deepcopy(configuration)
        if changed == "workflow":
            invalid["scope"]["workflow"] = "another-workflow"
        elif changed == "revision":
            invalid["scope"]["prompt_revision"] = "another-revision"
        elif changed == "contract":
            invalid["contract"]["operation_cost_upper_usd"] = "0.0001"
        elif changed == "ttl":
            invalid["scope"]["ttl_seconds"] = 59
        path = tmp_path / "private-invalid-cache.json"
        write_private(path, invalid)
        if changed == "oversized":
            path.write_bytes(b"x" * 16385)
        with pytest.raises(ValueError, match="PRIVATE_RUNTIME_CONFIGURATION_INVALID") as stopped:
            responses_create_from_scope_file(native, scope_file=path, **expected, **request())
        assert cache.scope.token.get_secret_value() not in str(stopped.value) and not calls
    finally:
        native.close()
