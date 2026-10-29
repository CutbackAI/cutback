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


def classify_stream(client, text: str, operation_id: UUID):
    """Ordinary native stream; partial text is not a completed application result."""
    pieces, complete = [], False
    with client.responses.create(
        model="gpt-6-luna",
        input=text,
        max_output_tokens=128,
        store=False,
        reasoning={"effort": "medium"},
        service_tier="default",
        stream=True,
        extra_headers=operation_headers(operation_id),
    ) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                pieces.append(event.delta)
            elif event.type == "response.completed":
                complete = event.response.status == "completed"
    if not complete:
        raise ValueError("GATEWAY_APPLICATION_RESPONSE_INCOMPLETE")
    return "".join(pieces)
