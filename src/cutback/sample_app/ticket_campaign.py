"""Independent native SDK ticket router; the sample runner supplies synthetic HTTP."""

from openai import OpenAI


def route_ticket(text: str, *, api_key: str, http_client) -> str:
    client = OpenAI(api_key=api_key, http_client=http_client, max_retries=0)
    try:
        response = client.responses.create(
            model="gpt-4.1",
            input=text,
            instructions="Route the support ticket. Invoice requests belong to billing, "
            "password reset requests belong to access, and failed export jobs belong to "
            "technical. Return exactly one of billing, access, technical with no explanation.",
            max_output_tokens=256,
            store=False,
        )
        return response.output_text
    finally:
        client.close()
