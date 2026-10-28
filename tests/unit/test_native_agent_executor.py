"""Actual pinned SDK parsing and request contracts; no vendor network access."""

import json

import httpx
import httpx2
import pytest
from support_native_agent import native_executor, response_body
from test_agent_turns import frame

from cutback.evals.agent_executor import strict_json
from cutback.evals.experiments import ModelSpec


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
@pytest.mark.parametrize(
    "mode",
    ["final", "tool", "parallel", "refused", "incomplete", "malformed", "outage", "unknown_usage"],
)
def test_native_agent_pins_tool_pairing_usage_refusals_no_retries_and_privacy(
    surface, mode, monkeypatch, caplog
):
    calls = []
    transport = httpx2 if surface == "messages.create" else httpx

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert "fixtures" not in json.dumps(body) and "assertions" not in json.dumps(body)
        assert "temperature" not in body and "stream" not in body
        if surface != "messages.create":
            assert body["store"] is False and body["parallel_tool_calls"] is False
        else:
            assert body["tool_choice"]["disable_parallel_tool_use"] is True
        if mode == "outage":
            return transport.Response(
                503, json={"error": {"type": "overloaded_error", "message": "PRIVATE_NATIVE_ERROR"}}
            )
        value = response_body(surface, mode)
        if mode == "unknown_usage":
            value.pop("usage")
        return transport.Response(200, json=value)

    executor = native_executor(surface, monkeypatch, handle)
    provider = "anthropic" if surface == "messages.create" else "openai"
    model = ModelSpec(
        key="owned",
        provider=provider,
        model="claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini",
    ).with_agent_turn(frame(surface))
    with caplog.at_level("DEBUG"):
        if mode == "outage":
            from anthropic import APIStatusError as AnthropicStatusError
            from openai import APIStatusError as OpenAIStatusError

            with pytest.raises((OpenAIStatusError, AnthropicStatusError)):
                executor.execute(model, "", 2)
        else:
            result = executor.execute(model, "", 2)
            assert result.agent_turn.finish == (
                "tool_calls"
                if mode == "tool"
                else "refused"
                if mode == "refused"
                else "incomplete"
                if mode in {"parallel", "malformed", "incomplete"}
                else "completed"
            )
            assert (result.cost_usd is None) == (mode == "unknown_usage")
    assert (
        len(calls) == 1
        and "PRIVATE_NATIVE_ERROR" not in caplog.text
        and "synthetic-A" not in caplog.text
    )
    executor.close()


def test_native_json_rejects_duplicate_keys_and_nonfinite_numbers():
    for value in ['{"ticket":"a","ticket":"b"}', '{"value":NaN}', '{"value":Infinity}']:
        with pytest.raises(ValueError):
            strict_json(value)
