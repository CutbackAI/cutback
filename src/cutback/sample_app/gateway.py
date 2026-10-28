"""Independent application: retain the native SDK and a stable per-task operation UUID."""

from uuid import UUID

from cutback.gateway.client import operation_headers


def classify(client, text: str, operation_id: UUID):
    response = client.responses.create(
        model="gpt-6-luna",
        input=text,
        max_output_tokens=128,
        store=False,
        reasoning={"effort": "medium"},
        service_tier="default",
        extra_headers=operation_headers(operation_id),
    )
    return response.output_text
