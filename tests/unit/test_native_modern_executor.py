"""New request contracts use actual pinned native SDKs and synthetic transports only."""

import importlib
import json
from decimal import Decimal
from importlib.metadata import version

import pytest

from cutback.core.native_models import NativeModelContract
from cutback.evals.executors import OpenAIExecutor
from cutback.evals.experiments import ModelSpec, upper_cost

pytestmark = pytest.mark.skipif(
    version("openai") not in {"2.29.0", "3.35.1"}, reason="separate native SDK matrix"
)


def model(effort="none", tier="default"):
    return ModelSpec(
        key="owned",
        provider="openai",
        model="gpt-6-luna",
        native_contract=NativeModelContract.current_luna_text(
            reasoning_effort=effort, service_tier=tier
        ),
        max_input_tokens=8192,
        max_output_tokens=256,
    )


def response():
    return {
        "id": "resp_synthetic_modern",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-luna",
        "status": "completed",
        "service_tier": "default",
        "output": [
            {
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "synthetic-label", "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_tokens_details": {
                "cached_tokens": 200,
                "cache_write_tokens": 700,
                "cache_write_tokens_details": {"ttl_30m_tokens": 500, "ttl_12h_tokens": 200},
            },
            "output_tokens_details": {"reasoning_tokens": 20},
        },
    }


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high", "xhigh", "max"])
@pytest.mark.parametrize("tier", ["auto", "default"])
def test_native_luna_request_freezes_defaults_bounds_storage_and_known_cost(
    monkeypatch, caplog, effort, tier
):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-sent")
    http = importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return http.Response(200, json=response())

    executor = OpenAIExecutor(http_client=http.Client(transport=http.MockTransport(handle)))
    spec = model(effort, tier)
    try:
        with caplog.at_level("DEBUG"):
            result = executor.execute(spec, "SYNTHETIC_PRIVATE_INPUT", 2)
        assert result.complete and result.output == "synthetic-label"
        assert result.cost_usd == Decimal("0.0001745")
        assert result.actual_model == "openai:gpt-6-luna"
        assert result.response_model == "gpt-6-luna"
        assert upper_cost(spec) == Decimal("0.002176") * (2 if tier == "auto" else 1)
        (body,) = calls
        assert body["reasoning"] == {"effort": effort} and body["service_tier"] == tier
        assert body["store"] is False and body["max_output_tokens"] == 256
        assert body["prompt_cache_options"] == {"ttl": "30m"}
        assert "tools" not in body and "stream" not in body
        assert executor.client.max_retries == 0
        assert "SYNTHETIC_PRIVATE" not in caplog.text and "synthetic-never-sent" not in caplog.text
    finally:
        executor.close()


@pytest.mark.parametrize(
    "damage",
    [
        "outage",
        "missing_tier",
        "fast",
        "missing_lifetime",
        "missing_usage",
        "wrong_model",
        "unqualified_tier",
    ],
)
def test_native_modern_uncertainty_is_one_call_not_a_guessed_price_or_retry(monkeypatch, damage):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-sent")
    http = importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")
    calls = []

    def handle(request):
        calls.append(request)
        wire = response()
        if damage == "outage":
            return http.Response(503, json={"error": {"message": "SYNTHETIC_PRIVATE_ERROR"}})
        if damage == "missing_tier":
            wire.pop("service_tier")
        elif damage == "fast":
            wire["service_tier"] = "priority"
        elif damage == "unqualified_tier":
            wire["service_tier"] = "standard_only"
        elif damage == "missing_lifetime":
            wire["usage"]["input_tokens_details"].pop("cache_write_tokens_details")
        elif damage == "wrong_model":
            wire["model"] = "gpt-4.1-mini"
        else:
            wire.pop("usage")
        return http.Response(200, json=wire)

    executor = OpenAIExecutor(http_client=http.Client(transport=http.MockTransport(handle)))
    try:
        if damage == "outage":
            from openai import APIStatusError

            with pytest.raises(APIStatusError):
                executor.execute(model(), "synthetic", 2)
        else:
            result = executor.execute(model(), "synthetic", 2)
            assert result.complete and result.cost_usd is None
            assert result.response_model == (
                "gpt-4.1-mini" if damage == "wrong_model" else "gpt-6-luna"
            )
        assert len(calls) == 1
    finally:
        executor.close()


@pytest.mark.parametrize("damage", ["price", "nontext"])
def test_invalid_approval_price_reference_is_rejected_before_native_http(monkeypatch, damage):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-never-sent")
    http = importlib.import_module("httpx2" if version("openai") == "3.35.1" else "httpx")
    calls = []

    def forbidden(request):
        calls.append(request)
        raise AssertionError("invalid contract must never reach the transport")

    executor = OpenAIExecutor(http_client=http.Client(transport=http.MockTransport(forbidden)))
    value = model()
    if damage == "price":
        value = value.model_copy(
            update={
                "native_contract": value.native_contract.model_copy(
                    update={"price_sha256": "0" * 64}
                )
            }
        )
    expected = (
        "MODERN_NATIVE_CONTRACT_CHANGED"
        if damage == "price"
        else "MODERN_NATIVE_TEXT_INPUT_REQUIRED"
    )
    try:
        with pytest.raises(ValueError, match=expected):
            executor.execute(
                value, "synthetic" if damage == "price" else [{"type": "input_image"}], 2
            )
        assert calls == []
    finally:
        executor.close()
