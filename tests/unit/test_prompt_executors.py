"""Pinned native clients with synthetic transports; never a paid provider request."""

import json
from decimal import Decimal
from uuid import uuid4

import httpx
import httpx2
import pytest

from cutback.core.prompts import PromptDocument, PromptGraph
from cutback.evals.executors import AnthropicExecutor, LocalExecutor, OpenAIExecutor
from cutback.evals.experiments import ModelSpec


def bound_model(provider, surface):
    identifier = uuid4()
    graph = PromptGraph(
        identifier,
        {
            identifier: PromptDocument(
                kind="chat",
                messages=[
                    {"role": "system", "content": "Classify {{input}} privately."},
                    {"role": "user", "content": "Return one label."},
                ],
                variables={"input": "string"},
                output_schema={
                    "type": "object",
                    "properties": {"label": {"type": "string"}},
                    "required": ["label"],
                    "additionalProperties": False,
                },
            )
        },
    )
    contract = graph.native_contract(
        {},
        surface=surface,
        sampling_options={"stop_sequences": ["END"]}
        if provider == "anthropic"
        else {"temperature": 0.2},
    )
    return ModelSpec(
        key="owned",
        provider=provider,
        model="claude-sonnet-4-6" if provider == "anthropic" else "gpt-4.1-mini",
        request_contract=contract,
        max_output_tokens=32,
        local_cost_upper_usd=Decimal(0) if provider == "local" else None,
    )


@pytest.mark.parametrize(
    "provider,surface",
    [
        ("openai", "responses.create"),
        ("openai", "chat.completions.create"),
        ("local", "chat.completions.create"),
    ],
)
def test_native_bundle_preserves_system_input_schema_and_no_storage(monkeypatch, provider, surface):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-owned")
    monkeypatch.setenv("CUTBACK_LOCAL_INFERENCE_ENDPOINT", "http://127.0.0.1:9191/v1")
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["temperature"] == 0.2
        messages = body["input" if surface == "responses.create" else "messages"]
        assert messages == [
            {"role": "system", "content": "Classify {{data}} privately."},
            {"role": "user", "content": "Return one label."},
        ]
        assert "tools" not in body and "stream" not in body
        assert "text" in body if surface == "responses.create" else "response_format" in body
        if provider == "openai":
            assert body["store"] is False
        if surface == "responses.create":
            return httpx.Response(
                200,
                json={
                    "id": "resp_synthetic",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-4.1-mini",
                    "status": "completed",
                    "output": [
                        {
                            "id": "msg_synthetic",
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": '{"label":"A"}', "annotations": []}
                            ],
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chat_synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"label":"A"}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            },
        )

    client = (LocalExecutor if provider == "local" else OpenAIExecutor)(
        http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    )
    try:
        result = client.execute(bound_model(provider, surface), "{{data}}", 2)
        assert result.complete and result.output == '{"label":"A"}'
        assert len(calls) == 1 and client.client.max_retries == 0
    finally:
        client.close()


@pytest.mark.parametrize("stop", ["end_turn", "max_tokens"])
def test_anthropic_native_system_is_separate_and_incomplete_is_not_success(monkeypatch, stop):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", "synthetic-owned")
    calls = []

    def handler(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["system"] == [{"type": "text", "text": "Classify synthetic privately."}]
        assert (
            body["stop_sequences"] == ["END"] and "temperature" not in body and "top_p" not in body
        )
        assert body["output_config"]["format"]["type"] == "json_schema"
        assert body["messages"] == [{"role": "user", "content": "Return one label."}]
        return httpx2.Response(
            200,
            json={
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "A"}],
                "stop_reason": stop,
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    monkeypatch.setattr(
        "cutback.evals.executors.anthropic_transport",
        lambda: httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False),
    )
    client = AnthropicExecutor()
    try:
        result = client.execute(bound_model("anthropic", "messages.create"), "synthetic", 2)
        assert result.complete == (stop == "end_turn")
        assert len(calls) == 1 and client.client.max_retries == 0
    finally:
        client.close()


def test_bound_prompt_outage_is_one_attempt_and_diagnostics_have_no_prompt(monkeypatch):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-owned")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "synthetic unavailable"}})

    client = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        with pytest.raises(Exception) as exc:
            client.execute(bound_model("openai", "responses.create"), "PRIVATE_CASE_VALUE", 2)
        assert "PRIVATE_CASE_VALUE" not in str(exc.value) and len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("kind", ["chat_refusal", "responses_refusal", "chat_tool"])
def test_refusals_and_unrequested_tools_cannot_pass_text_experiments(monkeypatch, kind):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "synthetic-owned")
    calls = []
    surface = "responses.create" if kind.startswith("responses") else "chat.completions.create"

    def handler(request):
        calls.append(request)
        if surface == "responses.create":
            body = {
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "completed",
                "output": [
                    {
                        "id": "msg_synthetic",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "A", "annotations": []},
                            {"type": "refusal", "refusal": "owned synthetic refusal"},
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            }
        else:
            message = {"role": "assistant", "content": "A"}
            if kind == "chat_refusal":
                message["refusal"] = "owned synthetic refusal"
            else:
                message["tool_calls"] = [
                    {
                        "type": "function",
                        "id": "tool_synthetic",
                        "function": {"name": "unqualified", "arguments": "{}"},
                    }
                ]
            body = {
                "id": "chat_synthetic",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4.1-mini",
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            }
        return httpx.Response(200, json=body)

    client = OpenAIExecutor(http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        result = client.execute(bound_model("openai", surface), "synthetic", 2)
        assert not result.complete and result.cost_usd is None and len(calls) == 1
    finally:
        client.close()


def test_unrequested_anthropic_tool_units_remain_unpriced_and_unexecuted(monkeypatch):
    monkeypatch.setenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", "synthetic-owned")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_synthetic",
                        "name": "unqualified",
                        "input": {},
                    },
                    {"type": "text", "text": "A"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )

    monkeypatch.setattr(
        "cutback.evals.executors.anthropic_transport",
        lambda: httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False),
    )
    client = AnthropicExecutor()
    try:
        result = client.execute(bound_model("anthropic", "messages.create"), "synthetic", 2)
        assert not result.complete and result.cost_usd is None and len(calls) == 1
    finally:
        client.close()
