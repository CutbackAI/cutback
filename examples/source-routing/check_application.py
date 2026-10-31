"""Check the actual native consumer offline. No provider credential or network call."""

import json
from pathlib import Path

import httpx
from app import classify
from openai import OpenAI


def main():
    calls = []

    def respond(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        calls.append(body)
        output = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
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
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 24, "output_tokens": 2, "total_tokens": 26},
            },
        )

    with OpenAI(
        api_key="synthetic-offline-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False),
    ) as client:
        for row in Path(__file__).with_name("tickets.jsonl").read_text("utf-8").splitlines():
            case = json.loads(row)
            before = len(calls)
            result = classify(client, case["input"])
            assert result.output_text == case["gold"] and result.status == "completed"
            assert len(calls) == before + 1
            assert calls[-1]["store"] is False
            assert calls[-1]["input"] == case["input"]
            assert calls[-1]["instructions"] == (
                "Classify the declared ticket priority. Return only Urgent or Normal."
            )
            assert calls[-1]["model"] in {"gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano"}
            assert 1 <= calls[-1]["max_output_tokens"] <= 64
        before = len(calls)
        try:
            classify(client, "x" * 4001)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected the unchanged application input bound")
        assert len(calls) == before
    print(
        "Native application contract passed offline; synthetic replies are not a model benchmark."
    )


if __name__ == "__main__":
    main()
