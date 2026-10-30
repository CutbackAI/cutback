"""Actual native SDK parsing through owned in-memory transport; zero vendor calls."""

import json
from pathlib import Path

import httpx
from app import classify, read_policy
from openai import OpenAI


def main():
    seen = []

    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["instructions"] == (
            "Classify the declared ticket priority. Return only Urgent or Normal."
        )
        seen.append(body["model"])
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_ticket",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "Urgent", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 12, "output_tokens": 2, "total_tokens": 14},
            },
        )

    policy = read_policy(Path(__file__).with_name("routing.json"))
    with OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(handle),
            trust_env=False,
        ),
    ) as client:
        for text in ["Priority: urgent. Ticket: AA.", "Priority: urgent. " + "details " * 30]:
            response = classify(client, text, policy)
            assert response.id == "resp_owned_ticket" and response.output_text == "Urgent"
        assert seen == ["gpt-4.1-mini", "gpt-4.1"]
        try:
            classify(client, "x" * 4001, policy)
        except ValueError:
            pass
        else:
            raise AssertionError("An input outside the approved policy must not dispatch")
        assert len(seen) == 2


if __name__ == "__main__":
    main()
