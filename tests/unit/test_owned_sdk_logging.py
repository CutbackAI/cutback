"""Actual pinned SDK DEBUG logging must not expose worker payloads or alter app logging."""

import logging

import httpx
import httpx2
import pytest
from anthropic import APIStatusError as AnthropicStatusError
from openai import APIStatusError as OpenAIStatusError
from test_prompt_executors import bound_model

from cutback.evals.executors import AnthropicExecutor, OpenAIExecutor


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("outage", [False, True])
def test_owned_native_payload_logging_is_suppressed_and_scope_resets(
    provider, outage, caplog, monkeypatch
):
    canary = "owned-request-and-provider-error-canary"
    monkeypatch.setenv("CUTBACK_EXPERIMENT_" + provider.upper() + "_KEY", "owned-synthetic-key")
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger=provider + "._base_client")
    transport = httpx if provider == "openai" else httpx2
    requests = []

    def handle(request):
        requests.append(request)
        assert canary.encode() in request.content
        if outage:
            return transport.Response(503, json={"error": {"message": canary}})
        if provider == "anthropic":
            return transport.Response(
                200,
                json={
                    "id": "msg_owned",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-6",
                    "content": [{"type": "text", "text": "A"}],
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            )
        return transport.Response(
            200,
            json={
                "id": "resp_owned",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "id": "msg_owned",
                        "content": [{"type": "output_text", "text": "A", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            },
        )

    if provider == "openai":
        executor = OpenAIExecutor(
            http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
        )
    else:
        monkeypatch.setattr(
            "cutback.evals.executors.anthropic_transport",
            lambda: httpx2.Client(transport=httpx2.MockTransport(handle), trust_env=False),
        )
        executor = AnthropicExecutor()
    try:
        model = bound_model(
            provider, "responses.create" if provider == "openai" else "messages.create"
        )
        if outage:
            with pytest.raises(OpenAIStatusError if provider == "openai" else AnthropicStatusError):
                executor.execute(model, canary, 2)
        else:
            assert executor.execute(model, canary, 2).output == "A"
        assert len(requests) == 1 and canary not in caplog.text
        logging.getLogger(provider + "._base_client").debug("owned-application-logging-choice")
        assert "owned-application-logging-choice" in caplog.text
    finally:
        executor.close()
