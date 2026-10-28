"""Real PostgreSQL concurrent application budgets, uncertainty, revocation and privacy."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from support_gateway import gateway_request, issue_gateway_key, native_response, prepare_gateway

from cutback.core.models import LLMEvent
from cutback.gateway.contracts import GatewayPolicy
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow
from cutback.server.gateway_metering import response_event
from cutback.server.gateway_service import GatewayError, finish, recover_leases, reserve

pytestmark = pytest.mark.postgres


def before_for(context, reservation):
    with context["app"].state.sessions() as session:
        row = session.scalar(
            select(StoredEvent).where(
                StoredEvent.project_id == context["project"],
                StoredEvent.event_id == reservation.event_id,
            )
        )
        return LLMEvent.model_validate(row.event)


def test_reserved_call_is_canonical_unknown_until_native_usage_settles(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    request = gateway_request()
    operation = uuid4()
    reservation = reserve(
        sessions,
        key["token"],
        operation,
        "a" * 64,
        request,
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
    )
    before = before_for(context, reservation)
    assert before.duration_ms is None and before.usage_source == "unavailable"
    assert before.trace_id == "1" * 32 and before.parent_span_id == "2" * 16
    forged = api[0].post(
        "/v1/ingest/events",
        headers=tenant[1],
        json={
            "project_id": str(context["project"]),
            "events": [before.model_dump(mode="json")],
        },
    )
    assert forged.status_code == 422
    detail = (
        api[0].get(context["path"] + f"/policies/{context['policy']['id']}", headers=api[1]).json()
    )
    assert all(row["active_requests"] == 1 for row in detail["budgets"])
    assert all(row["complete_modeled_cost_usd"] is None for row in detail["budgets"])
    event, amount, code = response_event(before, reservation.model, native_response(), 12.5, 200)
    assert amount == Decimal("0.000002") and code is None
    with localcontext() as money:
        money.prec = 6
        finish(sessions, reservation, amount, event=event)
    finish(sessions, reservation, amount, event=event)
    with sessions() as session:
        call = session.get(GatewayCallRow, (context["project"], operation))
        assert call.state == "SETTLED" and call.cost_usd == amount
        rows = session.scalars(
            select(GatewayBudgetRow).where(GatewayBudgetRow.project_id == context["project"])
        ).all()
        assert len(rows) == 4
        assert all(row.spent_usd == amount and row.reserved_usd == 0 for row in rows)
        assert all(row.active_requests == 0 and row.uncertain_requests == 0 for row in rows)
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == context["project"])
            )
            == 1
        )
    with pytest.raises(GatewayError, match="NO_REPLAY"):
        reserve(sessions, key["token"], operation, "a" * 64, request)
    with pytest.raises(GatewayError, match="ID_CONFLICT"):
        reserve(sessions, key["token"], operation, "b" * 64, request)
    metadata = api[0].get(context["path"] + "/keys", headers=tenant[2]).text
    assert key["token"] not in metadata and "digest" not in metadata
    exported = api[0].get(f"/v1/projects/{context['project']}/export", headers=api[1]).text
    assert all(
        table in exported
        for table in (
            '"gateway_calls"',
            '"gateway_keys"',
            '"gateway_budgets"',
            '"gateway_policies"',
        )
    )
    assert request.input not in exported and key["token"] not in exported
    assert "request_hmac" not in exported and '"digest"' not in exported


def test_concurrent_different_keys_share_project_team_and_model_dollars(api, tenant):
    context = prepare_gateway(api, tenant, cap="1.1")
    keys = [issue_gateway_key(context) for _ in range(4)]
    sessions = context["app"].state.sessions

    def attempt(index):
        try:
            return reserve(sessions, keys[index % 4]["token"], uuid4(), "a" * 64, gateway_request())
        except GatewayError as exc:
            assert exc.status == 429 and exc.code == "GATEWAY_MODELED_BUDGET_EXHAUSTED"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    finish(sessions, winners[0], None, code="GATEWAY_TEST_UNCERTAIN")
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(GatewayCallRow)
                .where(GatewayCallRow.project_id == context["project"])
            )
            == 1
        )
        rows = session.scalars(
            select(GatewayBudgetRow).where(
                GatewayBudgetRow.project_id == context["project"],
                GatewayBudgetRow.scope_kind != "key",
            )
        ).all()
        assert len(rows) == 3
        assert all(row.active_requests == 0 and row.uncertain_requests == 1 for row in rows)
        assert all(row.reserved_usd == Decimal("1.051536") for row in rows)
    with pytest.raises(GatewayError, match="MODELED_BUDGET_EXHAUSTED"):
        reserve(sessions, keys[1]["token"], uuid4(), "c" * 64, gateway_request())


def test_expired_dispatch_lease_keeps_money_and_late_usage_does_not_replay(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    reservation = reserve(sessions, key["token"], uuid4(), "a" * 64, gateway_request())
    assert recover_leases(sessions, project_id=context["project"]) == 0
    later = datetime.now(UTC) + timedelta(minutes=3)
    assert recover_leases(sessions, project_id=context["project"], now=later) == 1
    assert recover_leases(sessions, project_id=context["project"], now=later) == 0
    before = before_for(context, reservation)
    assert before.duration_ms is None
    event, amount, code = response_event(before, reservation.model, native_response(), 80, 200)
    finish(sessions, reservation, amount, code=code, event=event, now=later)
    assert recover_leases(sessions, project_id=context["project"], now=later) == 0
    with sessions() as session:
        rows = session.scalars(
            select(GatewayBudgetRow).where(GatewayBudgetRow.project_id == context["project"])
        ).all()
        assert all(row.reserved_usd == 0 and row.uncertain_requests == 0 for row in rows)
    with pytest.raises(GatewayError, match="NO_REPLAY"):
        reserve(sessions, key["token"], reservation.operation_id, "a" * 64, gateway_request())


def test_disabled_policy_cannot_reset_overlap_and_revoked_key_cannot_dispatch(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    client, admin, _ = api
    for headers in (tenant[1], tenant[2]):
        assert (
            client.post(
                context["path"] + "/policies",
                headers=headers,
                json=context["specification"].model_dump(mode="json"),
            ).status_code
            == 403
        )
    assert (
        client.post(
            context["path"] + f"/policies/{context['policy']['id']}/disable",
            headers=admin,
            json={
                "approval_hash": "0" * 64,
                "expected_state_version": context["policy"]["state_version"],
            },
        ).status_code
        == 409
    )
    assert (
        client.delete(context["path"] + f"/keys/{key['key']['id']}", headers=admin).status_code
        == 200
    )
    with pytest.raises(GatewayError, match="INVALID_OR_EXPIRED"):
        reserve(context["app"].state.sessions, key["token"], uuid4(), "a" * 64, gateway_request())
    assert (
        client.post(
            context["path"] + f"/policies/{context['policy']['id']}/disable",
            headers=admin,
            json={
                "approval_hash": context["policy"]["approval_hash"],
                "expected_state_version": context["policy"]["state_version"],
            },
        ).status_code
        == 200
    )
    replacement = GatewayPolicy.model_validate(
        {**context["specification"].model_dump(mode="python"), "policy_id": uuid4()}
    )
    created = client.post(
        context["path"] + "/policies", headers=admin, json=replacement.model_dump(mode="json")
    ).json()
    rejected = client.post(
        context["path"] + f"/policies/{created['id']}/approve",
        headers=admin,
        json={
            "approval_hash": created["approval_hash"],
            "approval_ref": str(uuid4()),
            "expected_state_version": created["state_version"],
        },
    )
    assert rejected.status_code == 409 and "MUST_NOT_OVERLAP" in rejected.text


def test_project_delete_waits_for_live_gateway_and_then_removes_every_table(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    reservation = reserve(sessions, key["token"], uuid4(), "a" * 64, gateway_request())
    client, admin, _ = api
    project_path = f"/v1/projects/{context['project']}"
    slug = client.get(project_path, headers=admin).json()["slug"]
    assert (
        client.delete(project_path, headers=admin, params={"confirm_slug": slug}).status_code == 409
    )
    finish(sessions, reservation, None, code="GATEWAY_TEST_UNCERTAIN")
    deleted = client.delete(project_path, headers=admin, params={"confirm_slug": slug})
    assert deleted.status_code == 200, deleted.text
    with sessions() as session:
        for cls in (GatewayCallRow, GatewayBudgetRow):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(cls)
                    .where(cls.project_id == context["project"])
                )
                == 0
            )
