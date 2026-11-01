"""Real PostgreSQL scope, conflicts, privacy, retention and unknown evidence."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update

from cutback.server.operations import maintain
from cutback.server.policy_activity_db import PolicyActivityRecord
from cutback.server.traces import fingerprint

pytestmark = pytest.mark.postgres


def event(**changes):
    now = time.time_ns()
    result = dict(
        event_id=str(uuid4()),
        environment="staging",
        service_name="owned-desk",
        workflow="owned-classify",
        call_site_id="site:" + "a" * 24,
        source_commit="b" * 40,
        policy_sha256="c" * 64,
        started_ns=now,
        ended_ns=now + 1000,
        cache_configured=True,
        cache_requested=True,
        state="hit",
        completion="completed",
        native_sdk_invocations=0,
        cache_operations=1,
        cache_write="not_attempted",
        usage_origin="historical_cached_response",
        cache_operation_upper_usd="0.000000123456",
        cache_cost_basis_ref="owned-upper",
    )
    result.update(changes)
    return result


def intake(api, tenant, *events, headers=None, project=None):
    return api[0].post(
        "/v1/ingest/policy-runtime",
        headers=headers or tenant[1],
        json={"project_id": project or tenant[0], "events": list(events)},
    )


def scope_body(**changes):
    now = datetime.now(UTC)
    result = dict(
        environment="staging",
        service_name="owned-desk",
        workflow="owned-classify",
        call_site_id="site:" + "a" * 24,
        source_commit="b" * 40,
        policy_sha256="c" * 64,
        window_start=(now - timedelta(hours=1)).isoformat(),
        window_end=(now + timedelta(minutes=1)).isoformat(),
    )
    result.update(changes)
    return result


def inspect(api, tenant, body=None, headers=None):
    return api[0].post(
        f"/v1/projects/{tenant[0]}/policy-runtime/inspect",
        headers=headers or tenant[2],
        json=body or scope_body(),
    )


def test_ingestion_current_scope_idempotence_atomic_conflicts_and_no_billing_rows(api, tenant):
    body = event()
    assert intake(api, tenant, body, headers=tenant[2]).status_code == 403
    assert intake(api, tenant, body, headers=api[1]).status_code == 403
    assert intake(api, tenant, body, project=str(uuid4())).status_code == 403
    assert intake(api, tenant, event(environment="other")).status_code == 403
    assert intake(api, tenant, body).json()["accepted"] == 1
    assert intake(api, tenant, body, body).json() == {
        "profile": "cutback.source-policy-runtime.v1",
        "accepted": 0,
        "duplicate": 2,
    }
    changed = {**body, "ended_ns": body["ended_ns"] + 1}
    assert intake(api, tenant, event(), changed).status_code == 409
    assert intake(api, tenant, changed, body).status_code == 409
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PolicyActivityRecord)
                .where(PolicyActivityRecord.project_id == UUID(tenant[0]))
            )
            == 1
        )
        from cutback.server.db import StoredEvent

        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == UUID(tenant[0]))
            )
            == 0
        )
    assert inspect(api, tenant, headers=tenant[1]).status_code == 403
    assert inspect(api, tenant).json()["retained_operations"] == 1


def test_concurrent_duplicate_identity_only_meters_one_operation(api, tenant):
    body = event()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: intake(api, tenant, body), range(8)))
    assert all(result.status_code == 200 for result in results)
    assert sum(result.json()["accepted"] for result in results) == 1
    assert inspect(api, tenant).json()["retained_operations"] == 1


def test_summary_preserves_unknowns_exact_costs_scopes_and_inactive_configuration(api, tenant):
    hit = event()
    inactive = event(
        cache_requested=False,
        state="inactive",
        native_sdk_invocations=1,
        cache_operations=0,
        cache_write="not_applicable",
        usage_origin="native_response",
    )
    assert intake(api, tenant, hit, inactive).status_code == 200
    body = scope_body()
    result = inspect(api, tenant, body).json()
    assert result["states"] == {"hit": 1, "inactive": 1}
    assert result["configured_operations"] == 2 and result["requested_cache_operations"] == 1
    assert result["native_sdk_invocations"] == 1 and result["cache_operations"] == 1
    assert isinstance(result["cache_component_upper_usd"], str)
    assert Decimal(result["cache_component_upper_usd"]) == Decimal("0.000000123456")
    assert (
        result["identity_basis"] == "client_reported_source_map"
        and result["capture_coverage"] == "unknown"
    )
    assert not result["content_included"] and not result["execution_authorized"]
    assert result["invoice_verified_cost_usd"] is result["business_task_success"] is None
    assert inspect(api, tenant, body).json()["raw_sha256"] == result["raw_sha256"]
    for field, value in (
        ("service_name", "other"),
        ("workflow", "other"),
        ("source_commit", "d" * 40),
        ("call_site_id", "other"),
        ("policy_sha256", "d" * 64),
    ):
        empty = inspect(api, tenant, {**body, field: value}).json()
        assert (
            empty["retained_operations"] == 0
            and empty["native_sdk_invocations"] is empty["cache_component_upper_usd"] is None
        )
    unknown = event(
        state="unknown",
        completion="failed",
        native_sdk_invocations=None,
        cache_operations=None,
        cache_write=None,
        usage_origin=None,
    )
    assert intake(api, tenant, unknown).status_code == 200
    result = inspect(api, tenant, body).json()
    assert (
        result["native_sdk_invocations"]
        is result["cache_operations"]
        is result["cache_component_upper_usd"]
        is None
    )
    assert result["known_native_sdk_invocations"] == 1 and result["unknown_native_operations"] == 1
    assert result["known_cache_operations"] == 1 and result["unknown_cache_operations"] == 1


def test_current_key_revocation_and_payload_refusal(api, tenant):
    created = (
        api[0]
        .post(f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": "ingest"})
        .json()
    )
    headers = {"Authorization": "Bearer " + created["token"]}
    assert intake(api, tenant, event(), headers=headers).status_code == 200
    assert (
        api[0]
        .delete(f"/v1/projects/{tenant[0]}/keys/{created['key_id']}", headers=api[1])
        .status_code
        == 200
    )
    assert intake(api, tenant, event(), headers=headers).status_code == 401
    assert intake(api, tenant, {**event(), "input": "OWNED_PRIVATE_CONTENT"}).status_code == 422
    assert intake(api, tenant, {**event(), "invoice_verified_cost_usd": "0"}).status_code == 422


def test_export_deletion_and_retention_do_not_invent_activation_after_raw_expiry(api, tenant):
    assert intake(api, tenant, event()).status_code == 200
    path = f"/v1/projects/{tenant[0]}"
    assert api[0].get(path + "/export", headers=tenant[2]).status_code == 403
    assert '"table": "source_policy_activity"' in api[0].get(path + "/export", headers=api[1]).text
    with api[2].state.sessions.begin() as session:
        session.execute(
            update(PolicyActivityRecord)
            .where(PolicyActivityRecord.project_id == UUID(tenant[0]))
            .values(received_at=datetime.now(UTC) - timedelta(days=40))
        )
    maintain(api[2].state.sessions, project_id=UUID(tenant[0]), apply=True)
    result = inspect(api, tenant).json()
    assert (
        result["retained_operations"] == 0
        and result["cache_operations"] is result["cache_component_upper_usd"] is None
    )
    assert intake(api, tenant, event()).status_code == 200
    slug = api[0].get(path, headers=api[1]).json()["slug"]
    assert api[0].delete(path, params={"confirm_slug": slug}, headers=api[1]).status_code == 200
    with api[2].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PolicyActivityRecord)
                .where(PolicyActivityRecord.project_id == UUID(tenant[0]))
            )
            == 0
        )


def test_truncated_retained_page_is_never_a_complete_runtime_cost_reference(api, tenant):
    from cutback.server.db import Environment

    base = event()
    with api[2].state.sessions.begin() as session:
        environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == UUID(tenant[0]), Environment.name == "staging"
            )
        )
        for offset in range(0, 10001, 200):
            records = []
            for index in range(offset, min(offset + 200, 10001)):
                raw = {
                    **base,
                    "event_id": str(uuid4()),
                    "started_ns": base["started_ns"] + index,
                    "ended_ns": base["ended_ns"] + index,
                }
                records.append(
                    dict(
                        id=uuid4(),
                        project_id=UUID(tenant[0]),
                        environment_id=environment,
                        event_id=UUID(raw["event_id"]),
                        workflow=raw["workflow"],
                        service_name=raw["service_name"],
                        source_commit=raw["source_commit"],
                        call_site_id=raw["call_site_id"],
                        policy_sha256=raw["policy_sha256"],
                        started_ns=raw["started_ns"],
                        fingerprint=fingerprint(raw),
                        metadata_record=raw,
                    )
                )
            session.execute(PolicyActivityRecord.__table__.insert(), records)
    result = inspect(api, tenant).json()
    assert result["truncated"] and result["retained_operations"] == result["sample_limit"] == 10000
    assert result["known_cache_operations"] == 10000
    assert (
        result["cache_operations"]
        is result["native_sdk_invocations"]
        is result["cache_component_upper_usd"]
        is None
    )
