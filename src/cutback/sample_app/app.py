"""Support-desk application under test. Native SDK only; no Cutback coupling."""

import json

from openai import OpenAI


def _analyze(ticket: str, *, api_key: str, http_client) -> dict:
    """Shared native SDK boundary used by two independent application workflows."""
    client = OpenAI(api_key=api_key, http_client=http_client, max_retries=0)
    try:
        response = client.responses.create(model="gpt-4.1", input=ticket, max_output_tokens=64)
        return json.loads(response.output_text)
    finally:
        client.close()


def triage(ticket: str, *, api_key: str, http_client) -> dict:
    """Classify a support ticket for the routing queue."""
    return _analyze(ticket, api_key=api_key, http_client=http_client)


def needs_escalation(ticket: str, *, api_key: str, http_client) -> bool:
    """Decide whether an owned synthetic ticket needs an urgent review."""
    return _analyze(ticket, api_key=api_key, http_client=http_client)["priority"] == "urgent"
