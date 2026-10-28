"""Independent native-SDK application used by the owned campaign example."""

from openai import OpenAI


def classify(text: str, *, api_key: str, http_client) -> str:
    client = OpenAI(api_key=api_key, http_client=http_client, max_retries=0)
    try:
        response = client.responses.create(
            model="gpt-4.1",
            input=text,
            instructions="Return the exact label A for this synthetic classification task.",
            max_output_tokens=256,
            store=False,
        )
        return response.output_text
    finally:
        client.close()
