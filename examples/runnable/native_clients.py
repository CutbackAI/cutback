"""Synthetic transports only. Every provider request is intercepted locally."""

import json

import httpx
import httpx2
from anthropic import Anthropic, AsyncAnthropic
from openai import AsyncOpenAI, OpenAI


def openai_fixture(request):
    if request.url.host != "api.openai.com":
        raise RuntimeError("Synthetic example refuses an unexpected provider host")
    body = json.loads(request.content)
    model = body["model"]
    if request.url.path == "/v1/responses":
        result = {
            "id": "resp_synthetic",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": model,
            "output": [
                {
                    "id": "msg_synthetic",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "synthetic answer", "annotations": []}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 20,
                "output_tokens": 3,
                "total_tokens": 23,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        frames = [
            {
                "type": "response.output_text.delta",
                "item_id": "msg_synthetic",
                "output_index": 0,
                "content_index": 0,
                "sequence_number": 0,
                "delta": "synthetic answer",
            },
            {"type": "response.completed", "response": result},
        ]
    elif request.url.path == "/v1/chat/completions":
        result = {
            "id": "chat_synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "synthetic answer"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "total_tokens": 23,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        frames = [
            {
                "id": "chat_synthetic",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": "synthetic answer"}, "finish_reason": "stop"}
                ],
            },
            {
                "id": "chat_synthetic",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": model,
                "choices": [],
                "usage": result["usage"],
            },
        ]
    else:
        raise RuntimeError("Synthetic example refuses an unexpected provider API")
    if body.get("stream"):
        return httpx.Response(
            200,
            content="".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
            + "data: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
    return httpx.Response(200, json=result)


def anthropic_fixture(request):
    if request.url.host != "api.anthropic.com" or request.url.path != "/v1/messages":
        raise RuntimeError("Synthetic example refuses an unexpected provider API")
    body = json.loads(request.content)
    result = {
        "id": "msg_synthetic",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": "synthetic answer"}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 20,
            "output_tokens": 3,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }
    if body.get("stream"):
        frames = [
            {
                "type": "message_start",
                "message": {
                    **result,
                    "content": [],
                    "stop_reason": None,
                    "usage": {**result["usage"], "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "synthetic answer"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 3},
            },
            {"type": "message_stop"},
        ]
        return httpx2.Response(
            200,
            content="".join(
                "event: " + frame["type"] + "\ndata: " + json.dumps(frame) + "\n\n"
                for frame in frames
            ),
            headers={"content-type": "text/event-stream"},
        )
    return httpx2.Response(200, json=result)


def openai_client(*, asynchronous=False):
    if asynchronous:
        return AsyncOpenAI(
            api_key="synthetic-not-a-key",
            base_url="https://api.openai.com/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(openai_fixture), trust_env=False
            ),
        )
    return OpenAI(
        api_key="synthetic-not-a-key",
        base_url="https://api.openai.com/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(openai_fixture), trust_env=False),
    )


def anthropic_client(*, asynchronous=False):
    if asynchronous:
        client = AsyncAnthropic(
            api_key="synthetic-not-a-key",
            base_url="https://api.anthropic.com",
            max_retries=0,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(anthropic_fixture), trust_env=False
            ),
        )
    else:
        client = Anthropic(
            api_key="synthetic-not-a-key",
            base_url="https://api.anthropic.com",
            max_retries=0,
            http_client=httpx2.Client(
                transport=httpx2.MockTransport(anthropic_fixture), trust_env=False
            ),
        )
    client._custom_headers = {}
    return client
