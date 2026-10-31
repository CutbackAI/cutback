"""Native response/privacy/retry boundaries of the explicit source consumer."""

import asyncio
import importlib.util
import json

import httpx
import pytest

from cutback.core.routing_producer import PROGRAM

openai = pytest.importorskip("openai")


def configuration():
    baseline = {
        "key": "baseline",
        "provider": "openai",
        "model": "gpt-4.1",
        "instruction": "Classify the owned ticket. Return only A or B.",
        "max_input_tokens": 8192,
        "max_output_tokens": 64,
    }
    return {
        "key": "owned-source-router",
        "provider": "router",
        "model": "input-size-v1",
        "routes": [
            {
                "max_input_bytes": 100,
                "target": {**baseline, "key": "small", "model": "gpt-4.1-mini"},
            },
            {"max_input_bytes": 4000, "target": baseline},
        ],
    }


def application(tmp_path):
    source = tmp_path / "app.py"
    source.write_text(PROGRAM, encoding="utf-8")
    (tmp_path / "routing.json").write_text(json.dumps(configuration()), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("owned_source_runtime", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def native(body, *, state="completed", refused=False):
    return httpx.Response(
        200,
        json={
            "id": "resp_owned_source",
            "object": "response",
            "created_at": 1,
            "status": state,
            "model": body["model"],
            "incomplete_details": {"reason": "max_output_tokens"}
            if state == "incomplete"
            else None,
            "output": [
                {
                    "id": "msg_owned_source",
                    "type": "message",
                    "role": "assistant",
                    "status": state,
                    "content": (
                        [{"type": "refusal", "refusal": "owned refusal"}]
                        if refused
                        else [{"type": "output_text", "text": "A", "annotations": []}]
                    ),
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 2, "total_tokens": 22},
        },
    )


@pytest.mark.parametrize(
    "state,refused", [("completed", False), ("incomplete", False), ("completed", True)]
)
def test_native_responses_are_preserved_and_exact_parameters_reach_only_one_target(
    tmp_path, state, refused
):
    module = application(tmp_path)
    calls = []

    def handle(request):
        assert str(request.url) == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        calls.append(body)
        assert body["input"] == "Owned short" and body["store"] is False
        assert body["instructions"] == configuration()["routes"][0]["target"]["instruction"]
        assert body["model"] == "gpt-4.1-mini" and body["max_output_tokens"] == 64
        return native(body, state=state, refused=refused)

    with openai.OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False),
    ) as client:
        result = module.classify(client, "Owned short")
        assert result.id == "resp_owned_source" and result.status == state
        assert type(result).__module__.startswith("openai.types.responses")
        assert len(calls) == 1
        if refused:
            assert result.output[0].content[0].refusal == "owned refusal"
        else:
            assert result.output_text == "A"


def test_customer_sdk_retries_keep_the_original_selected_target_without_cutback_failover(tmp_path):
    module = application(tmp_path)
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            changed = configuration()
            changed["routes"] = changed["routes"][1:]
            (tmp_path / "routing.json").write_text(json.dumps(changed), encoding="utf-8")
            return httpx.Response(
                500, headers={"retry-after": "0.001"}, json={"error": {"message": "owned outage"}}
            )
        return native(body)

    with openai.OpenAI(
        api_key="owned-synthetic-only",
        max_retries=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False),
    ) as client:
        assert module.classify(client, "Owned short").output_text == "A"
        assert len(calls) == 2 and calls[0] == calls[1]
        assert calls[1]["model"] == "gpt-4.1-mini" and client.max_retries == 1


def test_native_outage_propagates_without_adding_a_retry_or_baseline_call(tmp_path):
    module = application(tmp_path)
    calls = []

    def handle(request):
        calls.append(1)
        raise httpx.ConnectTimeout("owned outage", request=request)

    with openai.OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle), trust_env=False),
    ) as client:
        with pytest.raises(openai.APITimeoutError):
            module.classify(client, "Owned short")
        assert calls == [1]


def test_async_client_streaming_custom_endpoint_and_out_of_scope_inputs_refuse_before_dispatch(
    tmp_path,
):
    module = application(tmp_path)
    calls = []

    def forbidden(request):
        calls.append(1)
        pytest.fail("Unqualified input or client reached native dispatch")

    with openai.OpenAI(
        api_key="owned-synthetic-only",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(forbidden), trust_env=False),
    ) as client:
        for value in ["x" * 4001, "é" * 2001, "x" * 95905, {"unqualified": "frame"}]:
            with pytest.raises(ValueError):
                module.classify(client, value)
        with pytest.raises(TypeError):
            module.classify(client, "Owned", stream=True)
    with openai.OpenAI(
        api_key="owned-synthetic-only",
        base_url="https://gateway.example.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(forbidden), trust_env=False),
    ) as client:
        with pytest.raises(ValueError, match="OFFICIAL_NATIVE_ENDPOINT"):
            module.classify(client, "Owned")
    client = openai.AsyncOpenAI(
        api_key="owned-synthetic-only",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(forbidden), trust_env=False),
    )
    try:
        with pytest.raises(ValueError, match="QUALIFIED_SYNC_OPENAI_CLIENT"):
            module.classify(client, "Owned")
    finally:
        asyncio.run(client.close())
    assert not calls


@pytest.mark.parametrize("bad", ["duplicate", "private", "oversize", "other_provider"])
def test_bad_policy_refuses_without_disclosing_content_or_calling_a_provider(tmp_path, bad):
    module = application(tmp_path)
    marker = "owned-private-source-marker"
    config = configuration()
    if bad == "duplicate":
        raw = '{"key":"owned","key":"' + marker + '"}'
    elif bad == "private":
        raw = json.dumps({"private": marker})
    elif bad == "oversize":
        raw = marker + " " * 262145
    else:
        config["routes"][0]["target"].update(provider="anthropic", model="claude-haiku-4-5")
        raw = json.dumps(config)
    (tmp_path / "routing.json").write_text(raw, encoding="utf-8")
    with openai.OpenAI(
        api_key="owned-synthetic-only",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("No provider call allowed")),
            trust_env=False,
        ),
    ) as client:
        with pytest.raises(ValueError) as error:
            module.classify(client, "Owned")
        assert marker not in str(error.value) and "owned-synthetic-only" not in str(error.value)
