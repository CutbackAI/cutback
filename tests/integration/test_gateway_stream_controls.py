"""Real PG stream accounting, scope, migration and ASGI disconnect/backpressure."""

import asyncio
from dataclasses import replace
from decimal import Decimal
from importlib.resources import files
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from starlette.requests import ClientDisconnect
from support_gateway import gateway_request, issue_gateway_key, prepare_gateway

from cutback.core.models import LLMEvent
from cutback.gateway.app import strict_json
from cutback.gateway.contracts import ResponsesStream
from cutback.gateway.streaming import NativeStream
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow
from cutback.server.gateway_service import GatewayError, finish, record_first_text_delta, reserve

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    # This module exercises destructive downgrade/upgrade. Never share its DB
    # with another test's retained streaming intent or canonical reservations.
    return isolated_postgres_url


def reserved(context):
    key = issue_gateway_key(context)
    request = ResponsesStream.model_validate(gateway_request().model_dump() | {"stream": True})
    receipt = reserve(context["app"].state.sessions, key["token"], uuid4(), "a" * 64, request)
    with context["app"].state.sessions() as session:
        before = LLMEvent.model_validate(
            session.scalar(
                select(StoredEvent).where(
                    StoredEvent.project_id == context["project"],
                    StoredEvent.event_id == receipt.event_id,
                )
            ).event
        )
    return receipt, before


def test_stream_and_json_share_one_model_allowance(api, tenant):
    context = prepare_gateway(api, tenant, streaming=True, cap="1.1")
    receipt, _ = reserved(context)
    key = issue_gateway_key(context)
    sessions = context["app"].state.sessions
    with pytest.raises(GatewayError, match="BUDGET_EXHAUSTED"):
        reserve(sessions, key["token"], uuid4(), "b" * 64, gateway_request())
    finish(sessions, receipt, Decimal("0.000002"))
    json_receipt = reserve(sessions, key["token"], uuid4(), "b" * 64, gateway_request())
    with sessions() as session:
        models = session.scalars(
            select(GatewayBudgetRow).where(
                GatewayBudgetRow.project_id == context["project"],
                GatewayBudgetRow.scope_kind == "model",
            )
        ).all()
        assert len(models) == 1 and models[0].requests_used == 2
        assert (
            session.get(GatewayCallRow, (context["project"], json_receipt.operation_id)).streaming
            is False
        )
    finish(sessions, json_receipt, None)


def test_first_text_receipt_is_scoped_immutable_and_visible_without_payload(api, tenant):
    context = prepare_gateway(api, tenant, streaming=True)
    receipt, _ = reserved(context)
    sessions = context["app"].state.sessions
    for wrong in [
        replace(receipt, project_id=uuid4()),
        replace(receipt, key_id=uuid4()),
        replace(receipt, event_id=uuid4()),
    ]:
        with pytest.raises(GatewayError):
            record_first_text_delta(sessions, wrong, 1250)
    for invalid in [True, -1, 120000001, 1.5]:
        with pytest.raises(GatewayError, match="TIMING_OUTSIDE"):
            record_first_text_delta(sessions, receipt, invalid)
    record_first_text_delta(sessions, receipt, 1250)
    record_first_text_delta(sessions, receipt, 9999)
    finish(sessions, receipt, None)
    page = api[0].get(context["path"] + "/calls", headers=tenant[2])
    assert page.status_code == 200
    row = next(r for r in page.json()["items"] if r["operation_id"] == str(receipt.operation_id))
    assert row["streaming"] is True and row["first_text_delta_us"] == 1250
    assert "request_hmac" not in row and "input" not in row
    with pytest.raises(GatewayError, match="SCOPE_OR_STATE"):
        record_first_text_delta(sessions, receipt, 2000)


@pytest.mark.parametrize(
    "failure", ["slow_consumer", "disconnect", "no_text_timeout", "after_terminal", "cancelled"]
)
def test_asgi_cleanup_closes_upstream_and_preserves_known_final_usage(api, tenant, failure):
    import json
    import time

    from support_gateway import native_response

    context = prepare_gateway(api, tenant, streaming=True)
    receipt, before = reserved(context)
    closed = []

    class Source(httpx.AsyncByteStream):
        async def __aiter__(self):
            if failure == "no_text_timeout":
                await asyncio.sleep(5)
            yield b'data: {"type":"response.output_text.delta","delta":"A"}\n\n'
            if failure == "after_terminal":
                yield (
                    "data: "
                    + json.dumps({"type": "response.completed", "response": native_response()})
                    + "\n\n"
                ).encode()

        async def aclose(self):
            closed.append(1)

    async def invoke():
        upstream = httpx.Response(
            200,
            stream=Source(),
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )
        response = NativeStream(
            upstream,
            context["app"].state.sessions,
            receipt,
            before,
            1024,
            strict_json,
            time.perf_counter(),
            asyncio.get_running_loop().time() + (5 if failure == "cancelled" else 0.5),
        )
        sends = []
        reached = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body" and message.get("body"):
                sends.append(message)
                reached.set()
                if failure == "disconnect":
                    raise OSError("owned disconnected socket")
                if (
                    failure in {"slow_consumer", "cancelled"}
                    or failure == "after_terminal"
                    and len(sends) == 2
                ):
                    await asyncio.Event().wait()

        async def receive():
            await asyncio.Event().wait()

        if failure == "cancelled":
            task = asyncio.create_task(
                response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
            )
            await asyncio.wait_for(reached.wait(), timeout=4)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ClientDisconnect if failure == "disconnect" else TimeoutError):
                await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    asyncio.run(invoke())
    assert closed
    with context["app"].state.sessions() as session:
        call = session.get(GatewayCallRow, (context["project"], receipt.operation_id))
        assert call.state == ("SETTLED" if failure == "after_terminal" else "UNCERTAIN")
        assert (call.first_text_delta_us is None) == (failure == "no_text_timeout")
        budgets = session.scalars(
            select(GatewayBudgetRow).where(GatewayBudgetRow.project_id == context["project"])
        ).all()
        assert all(b.active_requests == 0 for b in budgets)
        assert all((b.reserved_usd == 0) == (failure == "after_terminal") for b in budgets)


@pytest.mark.parametrize("streaming", [False, True])
def test_additive_migration_preserves_legacy_truth_and_refuses_stream_intent_loss(
    api, tenant, monkeypatch, streaming
):
    context = prepare_gateway(api, tenant, streaming=streaming)
    url = context["app"].state.engine.url.render_as_string(hide_password=False)
    assert "/cutback_test_" in url
    monkeypatch.setenv("CUTBACK_DATABASE_URL", url)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    if streaming:
        with pytest.raises(RuntimeError, match="STREAMING_INTENT_OR_EVIDENCE"):
            command.downgrade(cfg, "0019")
    else:
        key = issue_gateway_key(context)
        call = reserve(
            context["app"].state.sessions, key["token"], uuid4(), "a" * 64, gateway_request()
        )
        finish(context["app"].state.sessions, call, None)
        command.downgrade(cfg, "0019")
        command.upgrade(cfg, "head")
        with context["app"].state.sessions() as session:
            row = session.get(GatewayCallRow, (context["project"], call.operation_id))
            assert row.streaming is None and row.first_text_delta_us is None
            assert row.state == "UNCERTAIN" and row.reserved_usd > 0
    with context["app"].state.sessions() as session:
        assert (
            session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0020"
        )
    policy = (
        api[0].get(context["path"] + "/policies", headers=context["headers"]).json()["items"][0]
    )
    assert policy["approval_hash"] == context["policy"]["approval_hash"]
