"""Pinned native SDK synthetic transport helpers."""

import json

import httpx
import httpx2

from cutback.evals import executors
from cutback.evals.agent_executor import NativeAgentExecutor


def response_body(surface, mode="final"):
    final = json.dumps({"answer": "Urgent", "citations": []}) if mode != "malformed" else "not json"
    calls = [
        {"call_id": "call_next", "tool": "lookup_ticket", "arguments": {"ticket": "synthetic-A"}}
    ]
    if mode == "parallel":
        calls.append({**calls[0], "call_id": "call_second"})
    tooling = mode in {"tool", "parallel"}
    if surface == "messages.create":
        content = (
            [
                {"type": "tool_use", "id": c["call_id"], "name": c["tool"], "input": c["arguments"]}
                for c in calls
            ]
            if tooling
            else [{"type": "text", "text": final}]
        )
        return {
            "id": "msg_owned",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "stop_reason": "tool_use"
            if tooling
            else "refusal"
            if mode == "refused"
            else "max_tokens"
            if mode == "incomplete"
            else "end_turn",
            "content": content,
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
    if surface == "chat.completions.create":
        message = {"role": "assistant", "content": None if tooling else final}
        if tooling:
            message["tool_calls"] = [
                {
                    "id": c["call_id"],
                    "type": "function",
                    "function": {"name": c["tool"], "arguments": json.dumps(c["arguments"])},
                }
                for c in calls
            ]
        if mode == "refused":
            message["refusal"] = "synthetic refusal"
        return {
            "id": "chat_owned",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls"
                    if tooling
                    else "length"
                    if mode == "incomplete"
                    else "stop",
                    "message": message,
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
    output = (
        [
            {
                "type": "function_call",
                "id": "fc_" + c["call_id"],
                "call_id": c["call_id"],
                "name": c["tool"],
                "arguments": json.dumps(c["arguments"]),
            }
            for c in calls
        ]
        if tooling
        else [
            {
                "id": "msg_owned",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "refusal", "refusal": "synthetic refusal"}]
                if mode == "refused"
                else [{"type": "output_text", "text": final, "annotations": []}],
            }
        ]
    )
    return {
        "id": "resp_owned",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4.1-mini",
        "status": "incomplete" if mode == "incomplete" else "completed",
        "output": output,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 10,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def native_executor(surface, monkeypatch, handler):
    executor = NativeAgentExecutor()
    if surface == "messages.create":
        monkeypatch.setenv("CUTBACK_EXPERIMENT_ANTHROPIC_KEY", "owned-never-sent")
        monkeypatch.setattr(
            executors,
            "anthropic_transport",
            lambda: httpx2.Client(transport=httpx2.MockTransport(handler), trust_env=False),
        )
        executor.clients["anthropic"] = executors.AnthropicExecutor()
    else:
        monkeypatch.setenv("CUTBACK_EXPERIMENT_OPENAI_KEY", "owned-never-sent")
        executor.clients["openai"] = executors.OpenAIExecutor(
            http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
        )
    return executor
