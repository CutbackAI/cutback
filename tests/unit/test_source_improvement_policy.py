"""Bounded source-policy data and real pinned SDK semantics over closed transports."""

import asyncio
import json
import logging

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI, OpenAI
from openai.types.responses import Response
from pydantic import ValidationError
from test_native_cache import native_body

from cutback.core.improvement_policy import SourceImprovementPolicy
from cutback.evals.experiments import ModelSpec
from cutback.source_policy import MAX_POLICY_BYTES, load_policy, responses_create


def policy(*, routed=False, cached=False):
    baseline = ModelSpec(
        key="reference",
        provider="openai",
        model="gpt-4.1",
        instruction="OWNED_PRIVATE_BASE_INSTRUCTION",
        max_output_tokens=64,
    )
    cheap = baseline.model_copy(
        update={
            "key": "cheap",
            "model": "gpt-4.1-mini",
            "instruction": "OWNED_PRIVATE_VARIANT",
            "max_output_tokens": 32,
        }
    )
    strategy = (
        ModelSpec(
            key="by-size",
            provider="router",
            model="input-size-v1",
            routes=[
                {"max_input_bytes": 64, "target": cheap},
                {"max_input_bytes": 128, "target": baseline},
            ],
        )
        if routed
        else cheap
    )
    return SourceImprovementPolicy(
        workflow="owned-ticket-priority",
        max_input_bytes=128,
        baseline=baseline,
        strategy=strategy,
        cache_execution={
            "freshness_ref": "owned-fixture",
            "ttl_seconds": 60,
            "content_retention_authorized": True,
            "immutable_or_safely_invalidated": True,
            "operation_cost_upper_usd": "0.000001",
            "operation_cost_basis_ref": "owned-fixture",
        }
        if cached
        else None,
    )


def native(handler, *, retries=0, endpoint="https://api.openai.com/v1/"):
    return OpenAI(
        api_key="owned-synthetic-no-provider-key",
        max_retries=retries,
        base_url=endpoint,
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False),
    )


@pytest.mark.parametrize("routed", [False, True])
def test_native_object_request_frame_and_no_payload_logs(routed, caplog):
    observed = []

    def provider(request):
        value = json.loads(request.content)
        observed.append(value)
        body = native_body()
        body["model"] = value["model"]
        return httpx.Response(200, json=body)

    client = native(provider)
    try:
        with caplog.at_level(logging.DEBUG):
            result = responses_create(client, "OWNED_PRIVATE_INPUT", policy(routed=routed))
            logging.getLogger("openai._base_client").debug("owned-outside-context")
        assert type(result) is Response and result.model == "gpt-4.1-mini"
        assert observed == [
            {
                "model": "gpt-4.1-mini",
                "input": "OWNED_PRIVATE_INPUT",
                "instructions": "OWNED_PRIVATE_VARIANT",
                "max_output_tokens": 32,
                "store": False,
            }
        ]
        assert (
            "OWNED_PRIVATE_INPUT" not in caplog.text and "OWNED_PRIVATE_VARIANT" not in caplog.text
        )
        assert "owned-outside-context" in caplog.text
        assert client.max_retries == 0
    finally:
        client.close()


def test_native_retries_preserved_without_fallback_or_extra_policy_attempts():
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503, json={"error": {"message": "owned provider unavailable"}})

    client = native(provider, retries=1)
    try:
        with pytest.raises(APIStatusError) as error:
            responses_create(client, "owned input", policy(routed=True))
        assert error.value.status_code == 503 and len(calls) == 2
        assert all(value["model"] == "gpt-4.1-mini" for value in calls)
        assert client.max_retries == 1
    finally:
        client.close()


def test_input_domain_and_utf8_size_refuse_before_native_io():
    calls = []
    client = native(lambda request: calls.append(request))
    try:
        for text in ("x" * 129, "é" * 65, None, "\ud800"):
            with pytest.raises(ValueError):
                responses_create(client, text, policy(routed=True))
        assert not calls
    finally:
        client.close()


def test_sync_entrypoint_refuses_async_stream_and_nonofficial_endpoint():
    calls = []
    async_client = AsyncOpenAI(
        api_key="owned-synthetic-only",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r))),
    )
    other = native(lambda r: calls.append(r), endpoint="https://example.invalid/v1/")
    try:
        with pytest.raises(ValueError, match="SYNCHRONOUS_CLIENT_REQUIRED"):
            responses_create(async_client, "owned", policy())
        with pytest.raises(TypeError):
            responses_create(other, "owned", policy(), stream=True)
        with pytest.raises(ValueError, match="OFFICIAL_NATIVE_ENDPOINT_REQUIRED"):
            responses_create(other, "owned", policy())
        assert not calls
    finally:
        asyncio.run(async_client.close())
        other.close()


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["strategy"].update(max_output_tokens=128),
        lambda value: value["strategy"].update(region="different"),
        lambda value: value.update(max_instruction_bytes=4),
        lambda value: value.update(max_input_bytes=True),
    ],
)
def test_changed_native_frame_or_unapproved_bounds_are_refused(change):
    value = policy().model_dump(mode="json")
    change(value)
    with pytest.raises((ValueError, ValidationError)):
        SourceImprovementPolicy.model_validate(value)


def test_bounded_config_reader_refuses_duplicate_keys_and_oversized_data(tmp_path):
    path = tmp_path / "policy.json"
    expected = policy(routed=True)
    path.write_text(expected.model_dump_json(), encoding="utf-8")
    assert load_policy(path) == expected
    path.write_text('{"workflow":"owned-secret","workflow":"duplicate"}', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_policy(path)
    assert "owned-secret" not in str(error.value)
    path.write_bytes(b" " * (MAX_POLICY_BYTES + 1))
    with pytest.raises(ValueError, match="CONFIGURATION_UNAVAILABLE_OR_INVALID"):
        load_policy(path)


def test_missing_native_usage_and_incomplete_status_are_not_reconstructed():
    body = native_body(status="incomplete", usage=False)
    client = native(lambda request: httpx.Response(200, json=body))
    try:
        result = responses_create(client, "owned", policy())
        assert type(result) is Response and result.usage is None
        assert result.status == "incomplete"
    finally:
        client.close()


@pytest.mark.parametrize("selected_cache", [False, True])
def test_cache_never_activates_from_ambient_configuration(monkeypatch, selected_cache):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json=native_body())

    def no_implicit_cache(*args, **kwargs):
        raise AssertionError(
            "cache activation requires an explicit selected contract and scope file"
        )

    monkeypatch.setenv("CUTBACK_NATIVE_CACHE_SCOPE_FILE", "owned-private-file-must-not-be-read")
    monkeypatch.setattr("cutback.native_cache.responses_create_from_scope_file", no_implicit_cache)
    client = native(provider)
    try:
        responses_create(client, "owned", policy(routed=True, cached=selected_cache))
        assert len(calls) == 1
    finally:
        client.close()
