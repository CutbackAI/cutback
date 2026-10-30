"""Independent owned record task and HTTP/native synthetic transport helpers."""

import hashlib
import json
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from support_native_agent import response_body

from cutback.core.isolated_episodes import IsolatedRecordScenario
from cutback.evals.datasets import Case
from cutback.tool_broker import BrokerClient, create_tool_broker
from cutback.tool_client import ToolBrokerClient
from cutback.tool_isolation import IsolatedToolSession


def record_case(project, principal, *, resource=None):
    resource = resource or uuid4()
    seeds = [
        {
            "id": str(resource),
            "project_id": str(project),
            "owner_id": str(principal),
            "fields": {
                "status": "open",
                "title": "Owned support case",
                "private_note": "SYNTHETIC_HIDDEN_FIELD",
            },
        },
        {
            "id": str(uuid4()),
            "project_id": str(uuid4()),
            "owner_id": str(principal),
            "fields": {"status": "open", "private_note": "SYNTHETIC_OTHER_PROJECT"},
        },
        {
            "id": str(uuid4()),
            "project_id": str(project),
            "owner_id": str(principal),
            "environment": "other-environment",
            "fields": {"status": "open", "private_note": "SYNTHETIC_OTHER_ENVIRONMENT"},
        },
    ]
    scenario = IsolatedRecordScenario.model_validate(
        {
            "seeds": seeds,
            "grants": [
                {
                    "resource_id": str(resource),
                    "read_fields": ["status", "title"],
                    "transitions": [{"field": "status", "before": "open", "after": "resolved"}],
                },
                *[
                    {"resource_id": seed["id"], "read_fields": ["private_note"]}
                    for seed in seeds[1:]
                ],
            ],
            "write_approved": True,
            "max_turns": 4,
            "max_tool_calls": 3,
            "assertions": {
                "required_tools": ["records_read", "records_patch"],
                "allowed_paths": [["records_read", "records_patch"]],
                "final_state": {
                    "/" + str(resource) + "/fields/status": "resolved",
                    "/" + str(resource) + "/version": 1,
                },
                "accepted_answers": ["Resolved"],
                "forbidden_tools": ["http_post", "shell"],
            },
        }
    )
    return Case(
        id="record-" + resource.hex, input="Resolve owned record " + str(resource), episode=scenario
    ).model_dump(mode="json")


def record_model_reply(request, *, surface="responses.create"):
    value = json.loads(request.content)
    serialized = json.dumps(value)
    assert "SYNTHETIC_HIDDEN_FIELD" not in serialized
    assert (
        "SYNTHETIC_OTHER_PROJECT" not in serialized
        and "SYNTHETIC_OTHER_ENVIRONMENT" not in serialized
    )
    assert "accepted_answers" not in serialized and "transitions" not in serialized
    import re

    resource = re.search(r"Resolve owned record ([a-f0-9-]{36})", serialized)[1]
    result_count = (
        serialized.count('"type": "function_call_output"')
        + serialized.count('"role": "tool"')
        + serialized.count('"type": "tool_result"')
    )
    body = response_body(surface, "tool" if result_count < 2 else "final")
    tool = "records_read" if result_count == 0 else "records_patch"
    arguments = (
        {"resource_id": resource}
        if result_count == 0
        else {
            "resource_id": resource,
            "expected_version": 0,
            "updates_json": '{"status":"resolved"}',
        }
    )
    if result_count < 2:
        if surface == "responses.create":
            body["output"][0].update(
                call_id="record-" + str(result_count), name=tool, arguments=json.dumps(arguments)
            )
        elif surface == "messages.create":
            body["content"][0].update(id="record-" + str(result_count), name=tool, input=arguments)
        else:
            body["choices"][0]["message"]["tool_calls"][0].update(
                id="record-" + str(result_count),
                function={"name": tool, "arguments": json.dumps(arguments)},
            )
    elif surface == "responses.create":
        body["output"][0]["content"][0]["text"] = json.dumps(
            {"answer": "Resolved", "citations": []}
        )
    elif surface == "messages.create":
        body["content"][0]["text"] = json.dumps({"answer": "Resolved", "citations": []})
    else:
        body["choices"][0]["message"]["content"] = json.dumps(
            {"answer": "Resolved", "citations": []}
        )
    return body


@contextmanager
def scoped_broker(
    project, principal, broker_id, image, *, endpoint, runtime_factory=IsolatedToolSession
):
    token = secrets.token_urlsafe(48)
    app = create_tool_broker(
        image_id=image,
        broker_id=broker_id,
        endpoint=endpoint,
        clients=[
            BrokerClient(
                project_id=project,
                principal_id=principal,
                workflows=("classify", "isolated-record-task"),
                token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        ],
        runtime_factory=runtime_factory,
    )
    with TestClient(app, base_url="https://owned-broker.invalid") as http:

        def dispatch(request):
            response = http.request(
                request.method,
                request.url.path,
                headers=dict(request.headers),
                content=request.content,
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=dict(response.headers)
            )

        client = ToolBrokerClient(
            "https://owned-broker.invalid",
            token,
            project_id=project,
            principal_id=principal,
            broker_id=broker_id,
            image_id=image,
            transport=httpx.MockTransport(dispatch),
        )
        try:
            yield client, app
        finally:
            client.close()
