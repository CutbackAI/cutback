import json
from decimal import Decimal

import httpx2
import pytest

from cutback.evals.executors import AnthropicExecutor
from cutback.evals.experiments import ModelSpec
from cutback.evals.planning import draft_plan


def test_anthropic_executor_uses_distinct_key_native_usage_and_no_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-must-not-be-used")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS", "Authorization: Bearer ambient-header-must-not-be-used"
    )
    monkeypatch.delenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", raising=False)
    with pytest.raises(ValueError, match="NO_ANTHROPIC"):
        AnthropicExecutor()
    monkeypatch.setenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", "synthetic-approved")
    original = httpx2.Client
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-approved"
        body = json.loads(request.content)
        assert body["max_tokens"] == 32
        if isinstance(body["system"], list):
            assert body["system"] == [
                {
                    "type": "text",
                    "text": "synthetic instruction",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]
        else:
            assert body["system"] == "synthetic instruction"
        assert "tools" not in body
        return httpx2.Response(
            200,
            json={
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "A"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    monkeypatch.setattr(
        "cutback.evals.executors.anthropic_transport",
        lambda: original(transport=httpx2.MockTransport(handler), trust_env=False),
    )
    executor = AnthropicExecutor()
    try:
        assert executor.client.max_retries == 0
        result = executor.execute(
            ModelSpec(
                key="baseline",
                provider="anthropic",
                model="claude-sonnet-4-6",
                instruction="synthetic instruction",
                max_output_tokens=32,
            ),
            "synthetic",
            2,
        )
        assert result.output == "A" and result.cost_usd == Decimal("0.0006")
        assert result.complete and result.input_tokens == 100
        assert len(requests) == 1
        from cutback.evals.experiments import upper_cost

        cached_model = ModelSpec(
            key="cached",
            provider="anthropic",
            model="claude-sonnet-4-6",
            instruction="synthetic instruction",
            max_output_tokens=32,
            prompt_cache="anthropic-1h",
        )
        assert upper_cost(cached_model) > upper_cost(
            cached_model.model_copy(update={"prompt_cache": None})
        )
        assert executor.execute(cached_model, "synthetic", 2).complete
        assert len(requests) == 2
    finally:
        executor.close()


def test_same_provider_candidate_planning_respects_task_evidence():
    dataset = {
        "hash": "a" * 64,
        "workflow": "classify",
        "provenance": {
            "independent_labels": True,
            "task_inference": {"suggested_metric": "exact_label"},
            "max_input_bytes": 100,
        },
    }
    baseline = ModelSpec(key="baseline", provider="anthropic", model="claude-sonnet-4-6")
    planned = draft_plan(dataset, baseline, minimum=0.9)
    assert planned.candidates[0].model == "claude-haiku-4-5"
    assert planned.metric.kind == "exact_label" and planned.budget.max_usd == 0
    assert draft_plan(dataset, baseline).metric.kind == "nonempty"
    dataset["provenance"]["max_input_bytes"] = 100000
    with pytest.raises(ValueError, match="INPUT_BOUND"):
        draft_plan(dataset, baseline)


def test_native_openai_worker_transport_is_private_bounded_and_marks_incomplete_output(monkeypatch):
    import httpx

    from cutback.evals.executors import OpenAIExecutor

    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-key")
    monkeypatch.setenv("OPENAI_ORG_ID", "ambient-org-must-not-be-used")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "ambient-project-must-not-be-used")
    calls = []

    def transport(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer owned-synthetic-key"
        assert (
            "openai-organization" not in request.headers and "openai-project" not in request.headers
        )
        body = json.loads(request.content)
        assert body["store"] is False and body["max_output_tokens"] == 32
        return httpx.Response(
            200,
            json={
                "id": "resp_owned",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "A", "annotations": []}],
                    }
                ],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    executor = OpenAIExecutor(
        http_client=httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
    )
    try:
        result = executor.execute(
            ModelSpec(key="owned", provider="openai", model="gpt-4.1-mini", max_output_tokens=32),
            "owned synthetic input",
            2,
        )
        assert not result.complete and result.cost_usd is None
        assert result.input_tokens == 10 and result.output_tokens == 2
        assert len(calls) == 1 and executor.client.max_retries == 0
    finally:
        executor.close()
