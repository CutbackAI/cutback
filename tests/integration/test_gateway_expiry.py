"""Expiry must be checked after a real shared mutation lock wait."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from support_gateway import gateway_request, issue_gateway_key, prepare_gateway

from cutback.server import gateway_service as service
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow

pytestmark = pytest.mark.postgres


def test_gateway_expiry_after_waiting_for_shared_project_lock(api, tenant, monkeypatch):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    clock = [datetime.now(UTC)]
    checked = Event()
    native_active_key = service.active_key

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    def active_key(session, token, now):
        row = native_active_key(session, token, now)
        checked.set()
        return row

    monkeypatch.setattr(service, "datetime", Clock)
    monkeypatch.setattr(service, "active_key", active_key)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with sessions.begin() as session:
            service.lock_project(session, context["project"])
            future = executor.submit(
                service.reserve, sessions, key["token"], uuid4(), "a" * 64, gateway_request()
            )
            assert checked.wait(5), "reservation never reached the pre-lock key check"
            clock[0] = context["specification"].expires_at + timedelta(seconds=1)
        with pytest.raises(service.GatewayError, match="GATEWAY_INVALID_OR_EXPIRED_KEY"):
            future.result(timeout=10)
    with sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(GatewayCallRow)
                .where(GatewayCallRow.project_id == context["project"])
            )
            == 0
        )
        assert all(
            value == 0
            for value in session.scalars(
                select(GatewayBudgetRow.requests_used).where(
                    GatewayBudgetRow.project_id == context["project"]
                )
            )
        )
