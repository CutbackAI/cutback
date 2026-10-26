"""Support-desk application under test. Native SDK only; no Cutback coupling."""

import json

from openai import OpenAI


def triage(ticket: str, *, api_key: str, http_client) -> dict:
    """Return a ticket category and priority; callers supply their SDK client."""
    client = OpenAI(api_key=api_key, http_client=http_client, max_retries=0)
    try:
        response = client.responses.create(model="gpt-4.1", input=ticket, max_output_tokens=64)
        return json.loads(response.output_text)
    finally:
        client.close()
