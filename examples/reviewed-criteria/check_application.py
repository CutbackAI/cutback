"""Run the independent native SDK application without a paid endpoint or API key."""

import httpx
from app import classify


def respond(request):
    assert str(request.url) == "https://api.openai.com/v1/responses"
    import json

    body = json.loads(request.content)
    assert body["model"] in {"gpt-4.1", "gpt-4.1-mini"}
    assert body["max_output_tokens"] == 64 and body["store"] is False
    return httpx.Response(
        200,
        json={
            "id": "resp_owned_criteria",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "gpt-4.1",
            "output": [
                {
                    "id": "msg_owned",
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"category":"billing","priority":"normal"}',
                            "annotations": [],
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 40, "output_tokens": 12, "total_tokens": 52},
        },
    )


if __name__ == "__main__":
    result = classify(
        "Owned ticket. Category: billing. Priority: normal.",
        api_key="synthetic-no-provider-key",
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
    )
    assert result == {"category": "billing", "priority": "normal"}
    print("Independent native synthetic classification passed; paid provider calls: 0.")
