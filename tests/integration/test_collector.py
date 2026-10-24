from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from cutback.server.db import AccessKey, StoredEvent

pytestmark = pytest.mark.postgres


def batch(project_id, event):
    return {
        "schema_version": "1.0",
        "project_id": project_id,
        "events": [event.model_dump(mode="json")],
    }


def test_auth_project_environment_scopes_and_hashed_keys(api, tenant, event):
    client, admin, application = api
    project, ingest, viewer = tenant
    body = batch(project, event)
    assert client.post("/v1/ingest/events", json=body).status_code == 401
    assert client.post("/v1/ingest/events", json=body, headers=viewer).status_code == 403
    assert (
        client.post(
            "/v1/ingest/events", json={**body, "project_id": str(uuid4())}, headers=ingest
        ).status_code
        == 403
    )
    other_environment = batch(project, event.model_copy(update={"environment": "production"}))
    assert (
        client.post("/v1/ingest/events", json=other_environment, headers=ingest).status_code == 403
    )
    assert client.get(f"/v1/projects/{project}/usage", headers=ingest).status_code == 403
    assert client.get(f"/v1/projects/{uuid4()}/usage", headers=viewer).status_code == 403
    with application.state.sessions() as session:
        keys = session.scalars(select(AccessKey).where(AccessKey.project_id == project)).all()
        assert len(keys) == 2
        assert all(len(key.digest) == 64 for key in keys)
        assert all(key.digest not in ingest["Authorization"] for key in keys)


def test_ingest_atomic_idempotent_precise_report_and_isolation(api, tenant, event):
    client, admin, application = api
    project, ingest, viewer = tenant
    response = client.post("/v1/ingest/events", json=batch(project, event), headers=ingest)
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1
    duplicate = client.post("/v1/ingest/events", json=batch(project, event), headers=ingest)
    assert duplicate.json()["duplicate"] == 1
    report = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()
    assert report["summary"]["observed_requests"] == 1
    assert report["summary"]["modeled_cost_usd"] == "0.000500000000"
    assert report["summary"]["actual_billed_cost_usd"] is None
    assert report["coverage"]["population_coverage_percent"] is None
    assert report["semantic_quality"] == "NOT VERIFIED"
    other = client.post(
        "/v1/projects", json={"slug": "other-" + uuid4().hex}, headers=admin
    ).json()["id"]
    other_viewer = client.post(
        f"/v1/projects/{other}/keys", json={"role": "viewer"}, headers=admin
    ).json()["token"]
    assert (
        client.get(
            f"/v1/projects/{project}/usage", headers={"Authorization": f"Bearer {other_viewer}"}
        ).status_code
        == 403
    )
    empty = client.get(f"/v1/projects/{other}/usage", headers=admin).json()
    assert empty["summary"]["observed_requests"] == 0
    assert empty["summary"]["modeled_cost_usd"] is None


def test_unknown_price_is_visible_and_never_summed_as_zero(api, tenant, event):
    client, _, _ = api
    project, ingest, viewer = tenant
    known = event.model_dump(mode="json")
    unknown = {**known, "event_id": str(uuid4()), "response_model": "unknown-model"}
    body = {"project_id": project, "events": [known, unknown]}
    assert client.post("/v1/ingest/events", json=body, headers=ingest).status_code == 200
    report = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()
    assert report["summary"]["modeled_cost_usd"] is None
    assert report["summary"]["known_modeled_cost_subtotal_usd"] == "0.000500000000"
    assert report["summary"]["unpriced_requests"] == 1
    assert report["unpriced_reasons"] == {"no_exact_rate": 1}


@pytest.mark.parametrize(
    "addition",
    [
        {"prompt": "PRIVATE-SECRET"},
        {"attributes": {"api_key": "PRIVATE-SECRET"}},
        {"raw_provider_usage": {"messages": "PRIVATE-SECRET"}},
        {"input_tokens": -1},
        {"event_schema_version": "99.0"},
        {"occurred_at": "2000-01-01T00:00:00Z"},
    ],
)
def test_invalid_content_and_schema_rejected_without_echo_or_partial_insert(
    api, tenant, event, addition
):
    client, _, application = api
    project, ingest, _ = tenant
    good = event.model_dump(mode="json")
    invalid = {**good, "event_id": str(uuid4()), **addition}
    response = client.post(
        "/v1/ingest/events", json={"project_id": project, "events": [good, invalid]}, headers=ingest
    )
    assert response.status_code == 422
    assert "PRIVATE" not in response.text
    with application.state.sessions() as session:
        assert not session.scalars(
            select(StoredEvent).where(StoredEvent.project_id == project)
        ).all()


def test_size_timestamp_revocation_and_error_contract(api, tenant, event):
    client, admin, _ = api
    project, ingest, _ = tenant
    huge = client.post(
        "/v1/ingest/events",
        content="x" * 1_048_577,
        headers={**ingest, "Content-Type": "application/json"},
    )
    assert huge.status_code == 413
    future = event.model_copy(update={"occurred_at": datetime.now(UTC) + timedelta(days=1)})
    response = client.post("/v1/ingest/events", json=batch(project, future), headers=ingest)
    assert response.status_code == 422
    assert set(response.json()) == {"code", "message", "request_id", "retryable"}
    key = client.post(f"/v1/projects/{project}/keys", json={"role": "ingest"}, headers=admin).json()
    assert (
        client.delete(f"/v1/projects/{project}/keys/{key['key_id']}", headers=admin).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/ingest/events",
            json=batch(project, event),
            headers={"Authorization": f"Bearer {key['token']}"},
        ).status_code
        == 401
    )


def test_health_and_metrics_scoped(api, tenant):
    client, admin, _ = api
    _, ingest, viewer = tenant
    assert client.get("/v1/health/live").status_code == 200
    assert client.get("/v1/health/ready").status_code == 401
    assert client.get("/v1/health/ready", headers=ingest).status_code == 200
    assert client.get("/metrics", headers=viewer).status_code == 403
    assert client.get("/metrics", headers=admin).status_code == 200
