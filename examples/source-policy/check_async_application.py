"""Check the actual async policy consumer with native objects and no provider I/O."""

import asyncio
import json
from pathlib import Path

import httpx
from async_app import classify
from openai import AsyncOpenAI
from openai.types.responses import Response

from cutback.source_policy import load_policy


async def main():
    policy = load_policy(Path(__file__).with_name("policy.json"))
    calls = []

    async def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        assert body == policy.request(body["input"])
        calls.append(body)
        label = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
        return httpx.Response(
            200,
            json={
                "id": "resp_owned_async",
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
                        "content": [{"type": "output_text", "text": label, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 24, "output_tokens": 2, "total_tokens": 26},
            },
        )

    async with AsyncOpenAI(
        api_key="owned-synthetic-only",
        max_retries=2,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False),
    ) as client:
        for line in Path(__file__).with_name("tickets.jsonl").read_text("utf-8").splitlines():
            case, before = json.loads(line), len(calls)
            result = await classify(client, case["input"])
            assert type(result) is Response and result.output_text == case["gold"]
            assert result.status == "completed" and len(calls) == before + 1
            assert client.max_retries == 2 and not client.is_closed()
        before = len(calls)
        try:
            await classify(client, "x" * (policy.max_input_bytes + 1))
        except ValueError:
            pass
        else:
            raise AssertionError("Unchanged input bound required")
        assert len(calls) == before
    print(
        "Offline async native contract passed. Synthetic output is not a real-model quality claim."
    )


if __name__ == "__main__":
    asyncio.run(main())
