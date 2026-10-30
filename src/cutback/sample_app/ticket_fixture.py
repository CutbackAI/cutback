"""Owned synthetic task rules, not observations of a real model or customer."""

SCENARIO = "synthetic-ticket-routing.v1"
INSTRUCTION = (
    "Return billing for invoice requests, access for password resets, "
    "or technical for failed export jobs. Output exactly one label."
)
TOPICS = (
    ("billing", "Please send a copy of my invoice for our finance review."),
    ("access", "My password reset link expired and I cannot sign in."),
    ("technical", "The export job fails with a timeout after processing the report."),
)
DETAILS = (
    (
        "for a purchase order",
        "with corrected tax details",
        "for last month's renewal",
        "with separate line items",
        "for an expense claim",
        "with our new company address",
        "for an annual subscription",
        "for a partial refund",
        "for two separate workspaces",
        "in the original currency",
        "with the payment date",
        "for a cancelled seat",
        "for a failed card payment",
        "with a missing discount",
        "for an audit request",
        "with a receipt attachment",
        "for a duplicate charge",
        "after a billing-contact change",
        "with the service period",
        "for an accounting reconciliation",
    ),
    (
        "after the link expired",
        "after an email-address change",
        "on a new device",
        "after a team invitation",
        "after resetting a security key",
        "after the browser closed",
        "with a blocked recovery email",
        "after an account lockout",
        "with an old bookmarked link",
        "for a newly added user",
        "after a workspace move",
        "with multiple recovery messages",
        "when the recovery email is delayed",
        "after the administrator restored access",
        "with a case-sensitive email address",
        "after returning from leave",
        "when the recovery code is rejected",
        "with an outdated recovery address",
        "after a session expired",
        "when a second browser is used",
    ),
    (
        "with an empty filter",
        "with a large date range",
        "with accented text",
        "with an empty result set",
        "with renamed columns",
        "with archived records",
        "with nested fields",
        "with a custom delimiter",
        "with quoted values",
        "with a time-zone filter",
        "with duplicate column names",
        "with a saved view",
        "with sorted rows",
        "with missing optional fields",
        "with multiple sheets",
        "with a shared report",
        "with a cancelled prior job",
        "with merged records",
        "with a custom date format",
        "with an attachment column",
    ),
)


def cases():
    result = []
    for index in range(240):
        topic = index % len(TOPICS)
        label, subject = TOPICS[topic]
        intent = (index // len(TOPICS)) % 20
        team = chr(65 + index // 26) + chr(65 + index % 26)
        result.append(
            {
                "id": f"ticket-{team}",
                "input": f"Ticket from synthetic team {team}: {subject}",
                "gold": label,
                "group": f"synthetic-intent-{topic}-{intent}",
            }
        )
        result[-1]["input"] += " Context: " + DETAILS[topic][intent] + "."
    return result


def expected(text):
    # This explicit task oracle is only for the owned mock provider fixture.
    if "invoice" in text:
        return "billing"
    if "password reset" in text:
        return "access"
    if "export job" in text:
        return "technical"
    raise ValueError("UNKNOWN_SYNTHETIC_TICKET")


APPLICATION_CHECK = """import json
import httpx
from app import route_ticket as classify

examples = {
    "Please send a copy of my invoice.": "billing",
    "My password reset link expired.": "access",
    "The export job fails with a timeout.": "technical",
}
requests = []
def respond(request):
    assert str(request.url) == "https://api.openai.com/v1/responses"
    body = json.loads(request.content)
    assert body["store"] is False and body["input"] in examples
    assert body["model"] in {"gpt-4.1", "gpt-4.1-mini"}
    assert 16 <= body["max_output_tokens"] <= 256
    requests.append(body)
    return httpx.Response(200, json={
        "id":"resp_ticket_check", "object":"response", "created_at":1,
        "model":body["model"], "status":"completed", "output":[{
            "id":"msg_owned", "type":"message", "role":"assistant", "status":"completed",
            "content":[{"type":"output_text", "text":examples[body["input"]], "annotations":[]}]
        }], "usage":{"input_tokens":20, "output_tokens":2, "total_tokens":22,
        "input_tokens_details":{"cached_tokens":0}, "output_tokens_details":{"reasoning_tokens":0}}
    })

def main():
    for text, label in examples.items():
        actual = classify(text, api_key="synthetic-no-provider-key",
            http_client=httpx.Client(transport=httpx.MockTransport(respond), trust_env=False))
        assert actual == label
    assert len(requests) == 3

if __name__ == "__main__":
    main()
"""
