"""Independent native SDK ticket classifier used by the reviewed-criteria sample."""

import json

from openai import OpenAI


def classify(ticket: str, *, api_key: str, http_client) -> dict:
    client = OpenAI(api_key=api_key, http_client=http_client, max_retries=0)
    try:
        response = client.responses.create(
            model="gpt-4.1", input=ticket, max_output_tokens=64, store=False
        )
        return json.loads(response.output_text)
    finally:
        client.close()
