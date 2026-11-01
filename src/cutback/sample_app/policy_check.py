"""Exercise the actual policy consumer and native response types without provider I/O."""

import json
import os
from pathlib import Path

import httpx
from app import classify
from openai import OpenAI
from openai.types.responses import Response

from cutback.source_policy import load_policy


def main():
    # This command checks native behavior offline. Private cache activation is
    # checked separately; an ambient runtime scope must not cause collector I/O.
    os.environ.pop("CUTBACK_NATIVE_CACHE_SCOPE_FILE", None)
    policy = load_policy(Path(__file__).with_name("policy.json"))
    calls = []

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body == policy.request(body["input"])
        calls.append(body)
        text = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
        return httpx.Response(
            200,
            json={
                "id": "resp_owned",
                "object": "response",
                "created_at": 1,
                "model": body["model"],
                "status": "completed",
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "output": [
                    {
                        "id": "msg_owned",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": text, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 24, "output_tokens": 2, "total_tokens": 26},
            },
        )

    with OpenAI(
        api_key="owned-synthetic-only",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
    ) as client:
        for line in Path(__file__).with_name("tickets.jsonl").read_text("utf-8").splitlines():
            case, before = json.loads(line), len(calls)
            result = classify(client, case["input"])
            assert isinstance(result, Response) and result.output_text == case["gold"]
            assert result.status == "completed" and len(calls) == before + 1
            assert client.max_retries == 2 and not client._client.is_closed
        before = len(calls)
        try:
            classify(client, "x" * (policy.max_input_bytes + 1))
        except ValueError:
            pass
        else:
            raise AssertionError("Unchanged input bound required")
        assert len(calls) == before
    print("Offline native contract passed. Synthetic output is not a real-model quality claim.")


if __name__ == "__main__":
    main()
