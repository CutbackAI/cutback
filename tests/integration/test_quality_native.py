"""Actual pinned native SDK and durable quality ledger, synthetic HTTP only."""

import json
from uuid import UUID

import httpx
import httpx2
import pytest
from sqlalchemy import select
from test_online_quality import captured, create_quality, quality_rows
from test_online_quality import quality_setup as quality_setup  # pytest fixture

from cutback.evals import executors
from cutback.server.quality_db import QualityEvaluation
from cutback.server.quality_worker import run_quality_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["success", "outage", "ungrounded", "unknown_usage"])
def test_pinned_native_quality_reservation_outage_privacy_and_evidence(
    api, tenant, quality_setup, monkeypatch, mode
):
    rule = create_quality(api, quality_setup, kind="model_judge")
    captured(api, tenant, quality_setup)
    monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-synthetic-never-sent")
    calls = []
    sessions = api[2].state.sessions

    def handle(request):
        calls.append(request)
        with sessions() as session:
            job = session.scalar(
                select(QualityEvaluation).where(QualityEvaluation.rule_id == UUID(rule["id"]))
            )
            assert job.charge_started and job.reserved_usd > 0
        body = json.loads(request.content)
        assert body["store"] is False and "tools" not in body
        assert "urgent" in json.dumps(body["input"])
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        if mode == "outage":
            return httpx.Response(429, json={"error": {"message": "synthetic private outage"}})
        content = json.dumps(
            {
                "outcome": "failed",
                "evidence_quote": "not in the captured source"
                if mode == "ungrounded"
                else "urgent",
            }
        )
        value = {
            "id": "resp_owned",
            "object": "response",
            "created_at": 1,
            "model": "gpt-4.1-mini",
            "status": "completed",
            "output": [
                {
                    "id": "msg_owned",
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": content, "annotations": []}],
                }
            ],
        }
        if mode != "unknown_usage":
            value["usage"] = {
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            }
        return httpx.Response(200, json=value)

    native = executors.OpenAIExecutor
    monkeypatch.setattr(
        executors,
        "OpenAIExecutor",
        lambda: native(
            http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
        ),
    )
    result = run_quality_one(sessions, quality_setup[2], execution_enabled=True)
    row = quality_rows(api, tenant, quality_setup, rule)[0]
    assert len(calls) == 1 and row["request_reserved"] and not row["output_retained"]
    assert result["status"] == ("failed" if mode == "success" else "error")
    assert (row["known_spent_usd"] is None) == (mode in {"outage", "unknown_usage"})
    assert "private outage" not in json.dumps(row)
    assert "urgent" not in json.dumps(row)
    assert run_quality_one(sessions, quality_setup[2], execution_enabled=True)["status"] == "IDLE"


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
@pytest.mark.parametrize("outage", [False, True])
def test_chat_and_messages_judges_use_native_structured_output_and_one_durable_attempt(
    api, tenant, quality_setup, monkeypatch, provider, outage
):
    model = "gpt-4.1-mini" if provider == "openai" else "claude-haiku-4-5"
    rule = create_quality(
        api,
        quality_setup,
        kind="model_judge",
        judge={
            "provider": provider,
            "model": model,
            "surface": "chat.completions.create" if provider == "openai" else "messages.create",
        },
    )
    captured(api, tenant, quality_setup)
    monkeypatch.setenv("CUTBACK_EXPERIMENT_" + provider.upper() + "_KEY", "owned-synthetic-native")
    calls = []
    sessions = api[2].state.sessions
    transport = httpx if provider == "openai" else httpx2

    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        with sessions() as session:
            job = session.scalar(
                select(QualityEvaluation).where(QualityEvaluation.rule_id == UUID(rule["id"]))
            )
            assert job.charge_started and job.reserved_usd > 0
        assert "urgent" in json.dumps(body["messages"])
        assert "tools" not in body and "stream" not in body
        if outage:
            return transport.Response(
                503,
                json={"error": {"type": "overloaded_error", "message": "owned synthetic outage"}},
            )
        content = '{"outcome":"passed","evidence_quote":"urgent"}'
        if provider == "anthropic":
            assert body["output_config"]["format"]["type"] == "json_schema"
            assert "temperature" not in body and "top_p" not in body
            return transport.Response(
                200,
                json={
                    "id": "msg_owned",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": content}],
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 10,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            )
        assert body["store"] is False and body["response_format"]["type"] == "json_schema"
        return transport.Response(
            200,
            json={
                "id": "chat_owned",
                "object": "chat.completion",
                "created": 1,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    if provider == "openai":
        native = executors.OpenAIExecutor
        monkeypatch.setattr(
            executors,
            "OpenAIExecutor",
            lambda: native(
                http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
            ),
        )
    else:
        monkeypatch.setattr(
            executors,
            "anthropic_transport",
            lambda: httpx2.Client(transport=httpx2.MockTransport(handle), trust_env=False),
        )
    result = run_quality_one(sessions, quality_setup[2], execution_enabled=True)
    assert len(calls) == 1 and result["status"] == ("error" if outage else "passed")
    row = quality_rows(api, tenant, quality_setup, rule)[0]
    assert (row["known_spent_usd"] is None) == outage
    rules = api[0].get(quality_setup[0] + "/quality-rules", headers=tenant[2]).json()["items"]
    authority = next(r for r in rules if r["id"] == rule["id"])
    assert authority["requests_used"] == 1
    assert authority["known_cost_requests"] == int(not outage)
    assert authority["unknown_or_pending_cost_requests"] == int(outage)
