"""Retained gateway truth, purpose separation and non-destructive migration recovery."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from support_gateway import gateway_request, issue_gateway_key, native_response, prepare_gateway

from cutback.core.models import LLMEvent
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow
from cutback.server.gateway_metering import response_event
from cutback.server.gateway_service import finish, reserve
from cutback.server.operations import maintain
from cutback.server.repository import PostgresEventRepository

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    """Migration guards must not inspect another feature's retained receipts."""
    return isolated_postgres_url


def test_key_purpose_separates_optimizer_overhead_without_claiming_an_internal_run(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context, traffic_kind="optimization")
    sessions = context["app"].state.sessions
    call = reserve(sessions, key["token"], uuid4(), "a" * 64, gateway_request())
    with sessions() as session:
        row = session.scalar(
            select(StoredEvent).where(StoredEvent.project_id == context["project"])
        )
        before = LLMEvent.model_validate(row.event)
        assert before.traffic_kind == "optimization" and before.experiment_run_id is None
    event, amount, code = response_event(before, call.model, native_response(), 25, 200)
    finish(sessions, call, amount, code=code, event=event)
    with sessions() as session:
        report = PostgresEventRepository().query_usage(
            session,
            context["project"],
            datetime.now(UTC) - timedelta(hours=1),
            datetime.now(UTC) + timedelta(hours=1),
            "traffic",
        )
        assert report["summary"]["observed_attempts"] == 1
        assert [row["key"] for row in report["groups"]] == ["optimization"]


def test_retention_keeps_unknown_money_and_late_settlement_cannot_reinsert_archived_event(
    api, tenant
):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    call = reserve(sessions, key["token"], uuid4(), "a" * 64, gateway_request())
    with sessions() as session:
        before = LLMEvent.model_validate(
            session.scalar(
                select(StoredEvent).where(StoredEvent.project_id == context["project"])
            ).event
        )
    future = datetime.now(UTC) + timedelta(days=3)
    result = maintain(
        sessions,
        now=future,
        raw_days=1,
        aggregate_days=10,
        apply=True,
        project_id=context["project"],
    )
    assert result["apply"]
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == context["project"])
            )
            == 0
        )
        record = session.get(GatewayCallRow, (context["project"], call.operation_id))
        assert record.state == "UNCERTAIN"
        budgets = session.scalars(
            select(GatewayBudgetRow).where(GatewayBudgetRow.project_id == context["project"])
        ).all()
        assert all(row.uncertain_requests == 1 and row.reserved_usd > 0 for row in budgets)
    event, amount, code = response_event(before, call.model, native_response(), 100, 200)
    finish(sessions, call, amount, code=code, event=event, now=future)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(StoredEvent)
                .where(StoredEvent.project_id == context["project"])
            )
            == 0
        )
        report = PostgresEventRepository().query_usage(
            session,
            context["project"],
            before.occurred_at.replace(hour=0, minute=0, second=0, microsecond=0),
            future,
            "model",
        )
        assert report["summary"]["observed_attempts"] == 1
        assert report["summary"]["modeled_cost_usd"] is None
        assert report["summary"]["latency_p95_ms"] is None
    maintain(
        sessions,
        now=future + timedelta(days=10),
        raw_days=1,
        aggregate_days=10,
        apply=True,
        project_id=context["project"],
    )
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


def test_gateway_intent_cannot_be_destroyed_by_downgrade(
    api_database_url, api, tenant, monkeypatch
):
    # Own the complete migration chain; later features can retain independent
    # receipts in the session database and must keep their stricter guards.
    prepare_gateway(api, tenant)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_GATEWAY_DATA"):
        command.downgrade(cfg, "0018")
    command.check(cfg)
