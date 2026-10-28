"""Real PostgreSQL pricing, immutable event valuation, scope and export."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select

from cutback.core.models import LLMEvent
from cutback.server.db import AuditEvent, RateOverride, StoredEvent

pytestmark = pytest.mark.postgres


def observed(event):
    return LLMEvent.model_validate(
        {
            **event.model_dump(mode="json"),
            "event_schema_version": "1.1",
            "requested_model": "gpt-6-luna",
            "response_model": "gpt-6-luna",
            "input_tokens": 20,
            "output_tokens": 5,
            "reasoning_tokens": 0,
            "cache_read_tokens": 2,
            "cache_write_tokens": 10,
            "cache_write_30m_tokens": 6,
            "cache_write_12h_tokens": 4,
            "service_tier": "default",
        }
    )


def test_modern_events_and_scoped_versioned_overrides_keep_prior_valuations(api, tenant, event):
    client, admin, app = api
    project, ingest, viewer = tenant
    base = f"/v1/projects/{project}"
    value = observed(event)
    batch = {"project_id": project, "events": [value.model_dump(mode="json")]}
    assert client.post("/v1/ingest/events", headers=ingest, json=batch).json()["accepted"] == 1
    prices = client.get(base + "/prices", headers=viewer).json()
    assert prices["catalog"]["snapshot_id"] == "public-text-2026-10-24-v2"
    assert [row["snapshot_id"] for row in prices["observation_catalogs"]] == [
        "public-text-2026-10-24-v2",
        "openai-text-2026-10-28-v1",
    ]
    override = {
        "provider": "openai",
        "model": "gpt-6-luna",
        "service_tier": "default",
        "input": "0.20",
        "output": "1.00",
        "cache_read": "0.02",
        "input_token_contract": "openai-inclusive-cache-writes-v2",
        "cache_write_30m": "0.25",
        "cache_write_12h": "0.50",
        "long_context_threshold": 272000,
        "long_context_input_multiplier": "2",
        "long_context_output_multiplier": "1.5",
        "valid_from": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
    }
    assert client.post(base + "/prices/overrides", headers=viewer, json=override).status_code == 403
    response = client.post(base + "/prices/overrides", headers=admin, json=override)
    assert response.status_code == 201, response.text
    override_id = response.json()["id"]
    second = value.model_copy(
        update={"event_id": uuid4(), "request_attempt_id": uuid4(), "span_id": "c" * 16}
    )
    response = client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [second.model_dump(mode="json")]},
    )
    assert response.status_code == 200 and response.json()["accepted"] == 1
    assert client.post("/v1/ingest/events", headers=ingest, json=batch).json()["duplicate"] == 1
    with app.state.sessions() as session:
        rows = {
            row.event_id: row
            for row in session.scalars(
                select(StoredEvent).where(StoredEvent.project_id == UUID(project))
            ).all()
        }
        assert len(rows) == 2
        assert rows[value.event_id].cost_usd == Decimal("0.00000507")
        assert rows[value.event_id].price_snapshot_id == "openai-text-2026-10-28-v1"
        assert rows[second.event_id].cost_usd == Decimal("0.00001014")
        assert rows[second.event_id].cost_status == "contract_rate"
        assert rows[second.event_id].price_snapshot_id == "project-override:" + override_id
        assert rows[second.event_id].event["cache_write_12h_tokens"] == 4
    usage = client.get(base + "/usage", headers=viewer).json()["summary"]
    assert usage["observed_attempts"] == usage["priced_requests"] == 2
    assert Decimal(usage["modeled_cost_usd"]) == Decimal("0.00001521")
    exported = client.get(base + "/export", headers=admin)
    assert exported.status_code == 200 and '"cache_write_12h_tokens": 4' in exported.text
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-pricing-" + uuid4().hex}
    ).json()["id"]
    assert client.get(f"/v1/projects/{other}/prices", headers=viewer).status_code == 403


def test_partial_lifetimes_remain_unpriced_and_incomplete_rate_contracts_are_rejected(
    api, tenant, event
):
    client, admin, _ = api
    project, ingest, viewer = tenant
    value = observed(event).model_copy(update={"cache_write_12h_tokens": None})
    reply = client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [value.model_dump(mode="json")]},
    )
    assert reply.status_code == 200, reply.text
    usage = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()["summary"]
    assert usage["unpriced_requests"] == 1 and usage["priced_requests"] == 0
    reply = client.post(
        f"/v1/projects/{project}/prices/overrides",
        headers=admin,
        json={
            "provider": "openai",
            "model": "gpt-6-luna",
            "input": "0.1",
            "output": "0.5",
            "cache_read": "0.01",
            "input_token_contract": "openai-inclusive-cache-writes-v2",
            "valid_from": datetime.now(UTC).isoformat(),
        },
    )
    assert reply.status_code == 422


def quote(event, **updates):
    return {
        "provider": "openai",
        "model": event.response_model,
        "input": "0.1",
        "output": "0.2",
        "cache_read": "0.01",
        "valid_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        **updates,
    }


def test_concurrent_quote_replay_is_one_record_and_conflicting_start_is_rejected(
    api, tenant, event
):
    from concurrent.futures import ThreadPoolExecutor

    client, admin, app = api
    project, _, viewer = tenant
    path = f"/v1/projects/{project}/prices"
    body = quote(event)
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(
            pool.map(lambda _: client.post(path + "/overrides", headers=admin, json=body), range(4))
        )
    assert [reply.status_code for reply in replies] == [201] * 4
    assert len({reply.json()["id"] for reply in replies}) == 1
    conflict = client.post(path + "/overrides", headers=admin, json={**body, "input": "0.2"})
    assert conflict.status_code == 409
    assert "CONTRACT_RATE_PERIOD_START_CONFLICT" in conflict.text
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(RateOverride)
                .where(RateOverride.project_id == UUID(project))
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.project_id == UUID(project),
                    AuditEvent.action == "rate_override_created",
                )
            )
            == 1
        )
    assert client.get(path + "?limit=101", headers=viewer).status_code == 422
    first = client.get(path + "?limit=1", headers=viewer).json()
    assert len(first["overrides"]) == 1 and first["next_after"] is None
    assert first["overrides"][0]["valid_from"] is not None


def test_quote_quota_keeps_replay_available_and_pages_stay_project_scoped(api, tenant, event):
    client, admin, app = api
    project, _, viewer = tenant
    path = f"/v1/projects/{project}/prices"
    body = quote(event)
    reply = client.post(path + "/overrides", headers=admin, json=body)
    assert reply.status_code == 201
    identity = reply.json()["id"]
    with app.state.sessions.begin() as session:
        session.execute(
            insert(RateOverride),
            [
                {
                    "id": uuid4(),
                    "project_id": UUID(project),
                    "provider": "openai",
                    "model": f"synthetic-quote-{index}",
                    "valid_from": datetime.now(UTC) - timedelta(days=1),
                    "rates": {"input": "1", "output": "2", "cache_read": "0.1"},
                    "approved_by": "synthetic-cap-fixture",
                }
                for index in range(9999)
            ],
        )
    replay = client.post(path + "/overrides", headers=admin, json=body)
    assert replay.status_code == 201 and replay.json()["id"] == identity
    blocked = client.post(path + "/overrides", headers=admin, json={**body, "model": "new-quote"})
    assert blocked.status_code == 409 and "PROJECT_RATE_OVERRIDE_QUOTA" in blocked.text
    first = client.get(path + "?limit=100", headers=viewer).json()
    second = client.get(path + "?limit=100&after=" + first["next_after"], headers=viewer).json()
    assert len(first["overrides"]) == len(second["overrides"]) == 100
    assert not (
        {row["id"] for row in first["overrides"]} & {row["id"] for row in second["overrides"]}
    )
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "synthetic-quote-scope-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.get(
            f"/v1/projects/{other}/prices?after=" + first["next_after"], headers=viewer
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/v1/projects/{other}/prices?after=" + first["next_after"], headers=admin
        ).json()["overrides"]
        == []
    )


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("ambiguous", "ambiguous_contract_rates"),
        ("tiny", "modeled_price_precision_unavailable"),
        ("overflow", "modeled_price_outside_storage_bounds"),
    ],
)
def test_unrepresentable_or_ambiguous_cost_is_unknown_not_zero_or_ingest_failure(
    api, tenant, event, mode, reason
):
    client, _, app = api
    project, ingest, viewer = tenant
    rates = {"input": "1", "output": "2", "cache_read": "0.1"}
    if mode == "tiny":
        rates = {"input": "0.000000000001", "output": "0", "cache_read": "0"}
    elif mode == "overflow":
        rates = {"input": "1000000", "output": "1000000", "cache_read": "1000000"}
        event = event.model_copy(update={"input_tokens": 10**12, "output_tokens": 10**12})
    with app.state.sessions.begin() as session:
        session.add_all(
            [
                RateOverride(
                    project_id=UUID(project),
                    provider=event.provider,
                    model=event.response_model,
                    valid_from=event.occurred_at - timedelta(days=1),
                    rates=rates,
                    approved_by="synthetic-legacy-quote-fixture",
                )
                for _ in range(2 if mode == "ambiguous" else 1)
            ]
        )
    reply = client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [event.model_dump(mode="json")]},
    )
    assert reply.status_code == 200 and reply.json()["accepted"] == 1
    with app.state.sessions() as session:
        row = session.scalar(select(StoredEvent).where(StoredEvent.project_id == UUID(project)))
        assert row.cost_usd is None and row.cost_status == "unpriced" and row.cost_reason == reason
    usage = client.get(f"/v1/projects/{project}/usage", headers=viewer).json()["summary"]
    assert usage["unpriced_requests"] == 1 and usage["priced_requests"] == 0
    assert usage["modeled_cost_usd"] is None
