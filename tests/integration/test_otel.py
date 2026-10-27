import copy
import json
import time
from uuid import UUID

import pytest
from sqlalchemy import select

from cutback.server.db import StoredEvent

pytestmark = pytest.mark.postgres
PATH = "/v1/ingest/otel/v1/traces"


def attr(key, value):
    return {
        "key": key,
        "value": {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value},
    }


def export(provider="openai", *, provenance=True):
    now = time.time_ns()
    attributes = [
        attr("gen_ai.provider.name", provider),
        attr("gen_ai.request.model", "gpt-4.1-mini"),
        attr("gen_ai.response.model", "gpt-4.1-mini"),
        attr("gen_ai.usage.input_tokens", 100),
        attr("gen_ai.usage.output_tokens", 20),
        attr(
            "cutback.sdk_surface",
            "messages.create" if provider == "anthropic" else "responses.create",
        ),
    ]
    if provenance:
        attributes += [
            attr("cutback.usage_source", "provider_reported"),
            attr("cutback.metering_status", "text_only"),
            attr("cutback.cache_read_tokens", 0),
            attr("cutback.reasoning_tokens", 0),
        ]
    span = {
        "traceId": "ABCDEF1234567890ABCDEF1234567890",
        "spanId": "ABCDEF1234567890",
        "startTimeUnixNano": str(now - 125000000),
        "endTimeUnixNano": str(now),
        "name": "THIS_NAME_IS_NOT_PERSISTED",
        "attributes": attributes,
        "status": {"code": 1, "message": "THIS_MESSAGE_IS_NOT_PERSISTED"},
    }
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        attr("service.name", "synthetic-otel"),
                        attr("deployment.environment.name", "staging"),
                    ]
                },
                "scopeSpans": [{"scope": {"name": "owned-synthetic-producer"}, "spans": [span]}],
            }
        ]
    }


def spans(body):
    return body["resourceSpans"][0]["scopeSpans"][0]["spans"]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_scoped_otel_profile_idempotent_private_and_explicit_provenance(api, tenant, provider):
    client, admin, app = api
    project, ingest, viewer = tenant
    body = export(provider)
    assert client.post(PATH, headers=viewer, json=body).status_code == 403
    assert client.post(PATH, headers=admin, json=body).status_code == 403
    for _ in range(2):
        response = client.post(PATH, headers=ingest, json=body)
        assert response.status_code == 200 and response.json() == {}, response.text
    with app.state.sessions() as session:
        rows = session.scalars(
            select(StoredEvent).where(StoredEvent.project_id == UUID(project))
        ).all()
        assert len(rows) == 1
        event = rows[0].event
        assert event["trace_id"] == spans(body)[0]["traceId"].lower()
        assert event["duration_ms"] == 125 and event["input_tokens"] == 100
        assert event["retry_number"] is None and event["traffic_kind"] == "unknown"
        assert "NOT_PERSISTED" not in json.dumps(event)
    report = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()
    assert report["summary"]["external_otel_observations"] == 1
    assert "aggregate retries" in report["coverage"]["external_span_semantics"]
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from cutback.server.operations import maintain

    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
    maintain(app.state.sessions, apply=True, project_id=UUID(project))
    archived = client.get(
        f"/v1/projects/{project}/usage",
        headers=viewer,
        params={"start": old.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()},
    ).json()
    assert archived["summary"]["external_otel_observations"] == 1


def test_otel_missing_provenance_stays_unknown_and_content_is_rejected_atomically(api, tenant):
    client, _, app = api
    project, ingest, viewer = tenant
    body = export(provenance=False)
    assert client.post(PATH, headers=ingest, json=body).status_code == 200
    report = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()
    assert report["summary"]["input_tokens_observed"] is None
    assert report["summary"]["modeled_cost_usd"] is None
    bad = copy.deepcopy(body)
    spans(bad).append(copy.deepcopy(spans(bad)[0]))
    spans(bad)[1]["attributes"].append(attr("gen_ai.input.messages", "OWNED_SECRET_FIXTURE"))
    response = client.post(PATH, headers=ingest, json=bad)
    assert response.status_code == 400 and "OWNED_SECRET_FIXTURE" not in response.text
    assert (
        client.get(f"/v1/projects/{project}/usage", headers=viewer).json()["summary"][
            "observed_attempts"
        ]
        == 1
    )
    with app.state.sessions() as session:
        assert "OWNED_SECRET_FIXTURE" not in str(
            session.scalars(
                select(StoredEvent).where(StoredEvent.project_id == UUID(project))
            ).all()
        )


@pytest.mark.parametrize(
    "mutation", ["events", "environment", "zero_id", "time", "integer", "too_many", "project"]
)
def test_otel_profile_denies_unsafe_or_ambiguous_inputs(api, tenant, mutation):
    client, _, _ = api
    body = export()
    span = spans(body)[0]
    expected = 400
    if mutation == "events":
        span["events"] = [{"name": "owned payload fixture"}]
    elif mutation == "environment":
        body["resourceSpans"][0]["resource"]["attributes"][1] = attr(
            "deployment.environment.name", "production"
        )
        expected = 403
    elif mutation == "zero_id":
        span["traceId"] = "0" * 32
    elif mutation == "time":
        span["endTimeUnixNano"] = "1"
    elif mutation == "integer":
        span["attributes"][3]["value"] = {"doubleValue": 1.25}
    elif mutation == "too_many":
        body["resourceSpans"][0]["scopeSpans"].append(
            {"spans": [copy.deepcopy(span) for _ in range(200)]}
        )
    else:
        body["project_id"] = str(UUID(int=1))
    assert client.post(PATH, headers=tenant[1], json=body).status_code == expected


def test_unsupported_otel_spans_are_explicit_partial_success(api, tenant):
    response = api[0].post(PATH, headers=tenant[1], json=export("unsupported-provider"))
    assert response.status_code == 200
    assert response.json()["partialSuccess"]["rejectedSpans"] == "1"


def test_otel_json_numeric_timestamps_and_empty_root_parent_are_supported(api, tenant):
    client = api[0]
    assert client.post(PATH, headers=tenant[1], json={}).json() == {}
    body = export()
    span = spans(body)[0]
    span["startTimeUnixNano"] = int(span["startTimeUnixNano"])
    span["endTimeUnixNano"] = int(span["endTimeUnixNano"])
    span["parentSpanId"] = ""
    assert client.post(PATH, headers=tenant[1], json=body).status_code == 200
