"""Independent synthetic classifier with a closed default provider transport."""

import json
from uuid import uuid4

import httpx
from openai import OpenAI


def fixture_response(request):
    if str(request.url) != "https://api.openai.com/v1/responses":
        raise ValueError("SYNTHETIC_REUSE_DESTINATION_REFUSED")
    body = json.loads(request.content)
    if body.get("store") is not False:
        raise ValueError("SYNTHETIC_REUSE_STORAGE_POLICY_REFUSED")
    return httpx.Response(
        200,
        json={
            "id": "resp_owned_reuse_" + uuid4().hex,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": body["model"],
            "parallel_tool_calls": False,
            "tool_choice": "none",
            "tools": [],
            "output": [
                {
                    "id": "msg_owned_reuse",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "billing", "annotations": []}],
                }
            ],
            # Declared synthetic usage, not a tokenizer or invoice measurement.
            "usage": {
                "input_tokens": 100,
                "output_tokens": 2,
                "total_tokens": 102,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        },
    )


client = OpenAI(
    api_key="owned-synthetic-only",
    max_retries=0,
    http_client=httpx.Client(transport=httpx.MockTransport(fixture_response), trust_env=False),
)


def classify_reuse(text):
    return client.responses.create(
        model="gpt-4.1-mini",
        input=text,
        instructions="Classify this owned synthetic billing task.",
        max_output_tokens=64,
        store=False,
    )
