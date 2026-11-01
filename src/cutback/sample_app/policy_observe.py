"""Independent process startup for synthetic policy observation; no paid provider I/O."""

import asyncio
import inspect
import json
from pathlib import Path

import httpx
from app import classify
from openai import AsyncOpenAI, OpenAI
from openai.types.responses import Response

from cutback.instrumentation import workflow
from cutback.source_policy import load_policy

native_calls = []


def reply(request):
    assert str(request.url) == "https://api.openai.com/v1/responses"
    body = json.loads(request.content)
    native_calls.append(1)
    label = "Urgent" if "Priority: urgent." in body["input"] else "Normal"
    return httpx.Response(
        200,
        json={
            "id": "resp_owned_observation",
            "object": "response",
            "created_at": 1,
            "model": body["model"],
            "status": "completed",
            "parallel_tool_calls": False,
            "tool_choice": "none",
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


def sync_run(texts, policy):
    with OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(reply), trust_env=False),
    ) as client:
        with workflow(policy.workflow):
            for text in texts:
                result = classify(client, text)
                assert type(result) is Response and result.status == "completed"


async def async_run(texts, policy):
    async with AsyncOpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply), trust_env=False),
    ) as client:
        with workflow(policy.workflow):
            for text in texts:
                result = await classify(client, text)
                assert type(result) is Response and result.status == "completed"


def main():
    root = Path(__file__).resolve().parent
    policy = load_policy(root / "policy.json")
    if policy.cache_execution is not None and inspect.iscoroutinefunction(classify):
        raise ValueError("POLICY_SOURCE_ASYNC_CACHE_RUNTIME_UNQUALIFIED")
    native_calls.clear()
    cases = [json.loads(line) for line in (root / "tickets.jsonl").read_text("utf-8").splitlines()]
    texts = [case["input"] for case in cases[:5]] + [cases[0]["input"]] * 2
    if inspect.iscoroutinefunction(classify):
        asyncio.run(async_run(texts, policy))
    else:
        sync_run(texts, policy)
    observer = globals().get("_cutback_observer")
    flushed = observer.flush(5) if observer is not None else None
    print(
        json.dumps(
            {
                "synthetic_only": True,
                "native_sdk_calls": len(native_calls),
                **(
                    {"logical_operations": len(texts), "cache_configured": True}
                    if policy.cache_execution is not None
                    else {}
                ),
                "paid_provider_calls": 0,
                "real_model_quality_verified": False,
                "production_hit_rate": None,
                "metadata_flush_complete": flushed,
                "observer_health": observer.health()
                if observer is not None
                else {"enabled": False},
                "note": (
                    "Observation requires reviewed external-startup instrumentation "
                    "and explicit collector configuration."
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
