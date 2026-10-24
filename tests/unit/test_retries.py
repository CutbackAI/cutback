import asyncio

import httpx
import openai
import pytest
from test_instrumentation import SYNTHETIC_RESPONSE, observer  # noqa: F401


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "http500"])
def test_retry_attempts_correlate_without_double_counting(observer, asynchronous, failure):  # noqa: F811
    count = 0

    def transport(request):
        nonlocal count
        count += 1
        if count == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("PRIVATE ERROR", request=request)
            return httpx.Response(
                500, headers={"retry-after-ms": "1"}, json={"error": {"message": "PRIVATE ERROR"}}
            )
        return httpx.Response(200, json=SYNTHETIC_RESPONSE)

    async def run():
        async with openai.AsyncOpenAI(
            api_key="synthetic",
            max_retries=1,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        ) as client:
            return await client.responses.create(model="gpt-4.1-mini", input="PRIVATE")

    if asynchronous:
        response = asyncio.run(run())
    else:
        with openai.OpenAI(
            api_key="synthetic",
            max_retries=1,
            http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        ) as client:
            response = client.responses.create(model="gpt-4.1-mini", input="PRIVATE")
    assert response.id == SYNTHETIC_RESPONSE["id"]
    first, second = observer.exporter.events
    assert first.retry_number == 0 and second.retry_number == 1
    assert first.logical_request_id == second.logical_request_id
    assert first.request_attempt_id != second.request_attempt_id
    assert first.status == "error" and first.input_tokens is None
    assert second.status == "success" and second.input_tokens == 1000
    assert "PRIVATE" not in first.model_dump_json()
