"""Installed-capable native SDK -> real gateway HTTP -> real PG -> report sample."""

from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from test_pipeline import server

from cutback.demo_gateway import run_sample
from cutback.server.gateway_db import GatewayKeyRow, GatewayPolicyRow


@pytest.mark.postgres
@pytest.mark.e2e
def test_independent_native_application_gateway_shared_budgets_and_report(api, tenant):
    with (
        server(api[2]) as endpoint,
        httpx.Client(trust_env=False, follow_redirects=False) as client,
    ):
        report = run_sample(api[2], endpoint, api[1]["Authorization"][7:], tenant[0], client=client)
    assert report["provider_fixture_requests"] == 2 and report["paid_provider_calls"] == 0
    assert report["duplicate_blocked"] and report["rotated_key_shared_limit_blocked"]
    assert len(report["receipts"]) == 2
    stream = next(r for r in report["receipts"] if r["streaming"] is True)
    assert 0 <= stream["first_text_delta_us"] < 120000000
    ordinary = next(r for r in report["receipts"] if r["streaming"] is False)
    assert ordinary["first_text_delta_us"] is None
    rows = {row["scope_kind"]: row for row in report["budgets"] if row["scope_kind"] != "key"}
    assert set(rows) == {"project", "team", "model"}
    assert all(
        row["uncertain_requests"] == 1 and row["requests_used"] == 2 for row in rows.values()
    )
    assert all(
        Decimal(row["known_modeled_spent_usd"]) == Decimal("0.000002") for row in rows.values()
    )
    assert all(row["complete_modeled_cost_usd"] is None for row in rows.values())
    assert all(
        Decimal(row["retained_reservations_usd"]) == Decimal("1.051536") for row in rows.values()
    )
    with api[2].state.sessions() as session:
        policy = session.get(GatewayPolicyRow, UUID(report["policy_id"]))
        assert policy.state == "DISABLED"
        for receipt in report["receipts"]:
            assert session.get(GatewayKeyRow, UUID(receipt["key_id"])).revoked_at is not None
