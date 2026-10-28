"""Gateway protocol, crash/capacity and authority acceptance against real PostgreSQL."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from support_gateway import gateway_request, issue_gateway_key, native_response, prepare_gateway

from cutback.gateway.app import MAX_RESPONSE_BYTES, create_gateway
from cutback.gateway.client import operation_headers
from cutback.server.gateway_db import GatewayBudgetRow, GatewayCallRow
from cutback.server.gateway_service import recover_leases

pytestmark = pytest.mark.postgres


def app_for(context, provider, **options):
    return create_gateway(
        enabled=True,
        database_url=context["app"].state.engine.url.render_as_string(hide_password=False),
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        allowed_project_ids=frozenset({context["project"]}),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
        **options,
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_usage",
        "wrong_model",
        "wrong_tier",
        "output_cap",
        "input_context",
        "tool_units",
        "surface",
        "encoding",
        "oversized",
    ],
)
def test_unqualified_native_response_retains_reserve_and_unsafe_protocol_blocks_policy(
    api, tenant, damage
):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    upstream = []

    class TooLarge(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * (MAX_RESPONSE_BYTES + 1)

    async def provider(request):
        upstream.append(1)
        assert request.headers["accept-encoding"] == "identity"
        body = native_response()
        if damage == "missing_usage":
            body.pop("usage")
        if damage == "wrong_model":
            body["model"] = "unqualified-owned-model"
        if damage == "wrong_tier":
            body["service_tier"] = "priority"
        if damage == "output_cap":
            body["usage"]["output_tokens"] = 1025
        if damage == "input_context":
            body["usage"]["input_tokens"] = 1050001
        if damage == "tool_units":
            body["output"] = [{"type": "web_search_call", "id": "owned"}]
        if damage == "surface":
            return httpx.Response(200, text="owned non-JSON surface")
        if damage == "encoding":
            # A stream response avoids eager client decompression before the gateway checks headers.
            return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=TooLarge())
        if damage == "oversized":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, stream=TooLarge()
            )
        return httpx.Response(200, json=body)

    gateway = app_for(context, provider)
    with TestClient(gateway, raise_server_exceptions=False) as client:
        headers = {"Authorization": "Bearer " + key["token"], **operation_headers()}
        response = client.post(
            "/v1/responses", headers=headers, json=gateway_request().model_dump(mode="json")
        )
        assert response.status_code == (
            502 if damage in {"surface", "encoding", "oversized"} else 200
        ), response.text
        assert response.headers["x-should-retry"] == "false"
        detail = (
            api[0]
            .get(context["path"] + f"/policies/{context['policy']['id']}", headers=api[1])
            .json()
        )
        assert detail["policy"]["state"] == ("APPROVED" if damage == "missing_usage" else "BLOCKED")
        assert all(row["uncertain_requests"] == 1 for row in detail["budgets"])
        assert all(row["complete_modeled_cost_usd"] is None for row in detail["budgets"])
        assert all(
            row["retained_reservations_usd"] == "1.051536000000" for row in detail["budgets"]
        )
        if damage != "missing_usage":
            resumed = api[0].post(
                context["path"] + f"/policies/{context['policy']['id']}/resume",
                headers=api[1],
                json={
                    "approval_hash": context["policy"]["approval_hash"],
                    "approval_ref": str(uuid4()),
                },
            )
            assert resumed.status_code == 200, resumed.text
        # Resuming a policy does not authorize replay of the prior operation.
        assert (
            client.post(
                "/v1/responses", headers=headers, json=gateway_request().model_dump(mode="json")
            ).status_code
            == 409
        )
        assert len(upstream) == 1


def test_body_schema_cannot_select_urls_tools_streams_storage_or_another_scope(api, tenant):
    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    upstream = []

    def provider(request):
        upstream.append(1)
        return httpx.Response(200, json=native_response())

    with TestClient(app_for(context, provider), raise_server_exceptions=False) as client:
        base = gateway_request().model_dump(mode="json")
        for change in (
            {"tools": []},
            {"stream": True},
            {"store": True},
            {"service_tier": "auto"},
            {"base_url": "https://unapproved.invalid"},
            {"project_id": str(uuid4())},
            {"input": [{"type": "input_image", "image_url": "https://unapproved.invalid"}]},
        ):
            response = client.post(
                "/v1/responses",
                json={**base, **change},
                headers={"Authorization": "Bearer " + key["token"], **operation_headers()},
            )
            assert response.status_code == 422 and response.headers["x-should-retry"] == "false"
        raw = json.dumps(base).removesuffix("}") + ',"model":"gpt-6-luna"}'
        assert (
            client.post(
                "/v1/responses",
                content=raw,
                headers={
                    "content-type": "application/json",
                    "Authorization": "Bearer " + key["token"],
                    **operation_headers(),
                },
            ).status_code
            == 422
        )
    assert upstream == []
    with context["app"].state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(GatewayCallRow)
                .where(GatewayCallRow.project_id == context["project"])
            )
            == 0
        )


def test_native_async_cancellation_and_process_capacity_never_release_uncertain_money(api, tenant):
    from openai import APIStatusError, AsyncOpenAI

    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    dispatched = asyncio.Event()
    upstream = []

    async def provider(request):
        upstream.append(1)
        dispatched.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled provider must not resume")

    app = app_for(context, provider, max_inflight=1)

    async def exercise():
        async with app.router.lifespan_context(app):
            native = AsyncOpenAI(
                api_key=key["token"],
                base_url="https://owned-gateway.invalid/v1",
                http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)),
            )
            try:
                options = dict(
                    model="gpt-6-luna",
                    input="owned cancellation",
                    max_output_tokens=128,
                    store=False,
                    service_tier="default",
                    reasoning={"effort": "medium"},
                    extra_headers=operation_headers(),
                )
                task = asyncio.create_task(native.responses.create(**options))
                await asyncio.wait_for(dispatched.wait(), timeout=10)
                with pytest.raises(APIStatusError) as busy:
                    await native.responses.create(
                        **{**options, "extra_headers": operation_headers()}
                    )
                assert busy.value.status_code == 503
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.to_thread(
                    recover_leases,
                    context["app"].state.sessions,
                    project_id=context["project"],
                    now=datetime.now(UTC) + timedelta(minutes=3),
                )
                with pytest.raises(APIStatusError) as duplicate:
                    await native.responses.create(**options)
                assert duplicate.value.status_code == 409
            finally:
                await native.close()

    asyncio.run(exercise())
    assert len(upstream) == 1
    with context["app"].state.sessions() as session:
        rows = session.scalars(
            select(GatewayBudgetRow).where(GatewayBudgetRow.project_id == context["project"])
        ).all()
        assert all(row.active_requests == 0 and row.uncertain_requests == 1 for row in rows)
        assert all(str(row.reserved_usd) == "1.051536000000" for row in rows)


def test_revoked_approver_and_operator_project_allowlist_are_enforced(api, tenant):
    issued = (
        api[0]
        .post(f"/v1/projects/{tenant[0]}/keys", headers=api[1], json={"role": "project_admin"})
        .json()
    )
    context = prepare_gateway(api, tenant, actor={"Authorization": "Bearer " + issued["token"]})
    key = issue_gateway_key(context)
    upstream = []

    def provider(request):
        upstream.append(1)
        return httpx.Response(200, json=native_response())

    settings = dict(
        enabled=True,
        database_url=context["app"].state.engine.url.render_as_string(hide_password=False),
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        allowed_project_ids=frozenset({uuid4()}),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
    )
    with TestClient(create_gateway(**settings), raise_server_exceptions=False) as client:
        denied = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key["token"], **operation_headers()},
            json=gateway_request().model_dump(mode="json"),
        )
        assert denied.status_code == 403 and "NOT_AUTHORIZED_BY_OPERATOR" in denied.text
    api[0].delete(f"/v1/projects/{tenant[0]}/keys/{issued['key_id']}", headers=api[1])
    with TestClient(app_for(context, provider), raise_server_exceptions=False) as client:
        denied = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key["token"], **operation_headers()},
            json=gateway_request().model_dump(mode="json"),
        )
        assert denied.status_code == 403 and "CURRENT_ADMIN_REQUIRED" in denied.text
    assert upstream == []
