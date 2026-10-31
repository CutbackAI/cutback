"""Independent synthetic classifier. Its default transport cannot contact a provider."""

import httpx
from openai import OpenAI

client = OpenAI(
    api_key="owned-synthetic-only",
    max_retries=0,
    http_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))),
)


def classify(text):
    return client.responses.create(
        model="gpt-4.1",
        input=text,
        instructions="Classify this owned synthetic billing task.",
        max_output_tokens=64,
        store=False,
    )
