"""Actual native SDK retry/type boundaries over ASGI, with synthetic provider HTTP and real PG."""

import asyncio
import importlib
from importlib.metadata import version
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select
from support_gateway import issue_gateway_key, native_response, prepare_gateway

from cutback.gateway.app import create_gateway
from cutback.gateway.client import operation_headers
from cutback.instrumentation.openai import OpenAIInstrumentation
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayCallRow

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("scenario", ["success", "rate_limit", "outage", "lost_ack"])
@pytest.mark.parametrize("transport_name", ["httpx", "httpx2"])
def test_native_response_retry_loss_and_explicit_observation_delegation(
    api, tenant, monkeypatch, scenario, transport_name, asynchronous
):
    if transport_name == "httpx2" and version("openai") != "3.35.1":
        pytest.skip("native HTTPX2 transport requires the separately pinned OpenAI 3 matrix")
    from openai import AsyncOpenAI, OpenAI

    context = prepare_gateway(api, tenant)
    key = issue_gateway_key(context)
    http = importlib.import_module(transport_name)
    upstream_calls, front_calls, emitted = [], [], []

    async def provider(request):
        upstream_calls.append(request)
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer owned-synthetic-provider-key-only"
        assert "openai-project" not in request.headers
        assert "x-cutback-operation-id" not in request.headers
        if scenario == "outage":
            raise httpx.ReadTimeout("synthetic transport loss", request=request)
        if scenario == "rate_limit":
            return httpx.Response(429, json={"error": {"message": "owned synthetic rate limit"}})
        return httpx.Response(200, json=native_response())

    gateway = create_gateway(
        database_url=context["app"].state.engine.url.render_as_string(hide_password=False),
        enabled=True,
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
        allowed_project_ids=frozenset({context["project"]}),
    )
    with TestClient(gateway, raise_server_exceptions=False) as gateway_client:

        def bridge(request):
            front_calls.append(request)
            result = gateway_client.post(
                "/v1/responses", content=request.content, headers=dict(request.headers)
            )
            assert result.headers["x-should-retry"] == "false"
            if scenario == "lost_ack" and len(front_calls) == 1:
                raise http.ReadTimeout("owned lost client acknowledgement", request=request)
            return http.Response(
                result.status_code, headers=dict(result.headers), content=result.content
            )

        async def exercise():
            async def invoke():
                result = native.responses.create(**kwargs)
                return await result if asynchronous else result

            Native = AsyncOpenAI if asynchronous else OpenAI
            HttpClient = http.AsyncClient if asynchronous else http.Client
            native = Native(
                api_key=key["token"],
                base_url="https://owned-gateway.invalid/v1",
                http_client=HttpClient(transport=http.MockTransport(bridge)),
                organization="must-not-forward",
                project="must-not-forward",
            )
            observer = OpenAIInstrumentation(
                service_name="owned-application",
                environment="staging",
                endpoint="http://127.0.0.1:1/v1/ingest/events",
                project_id=str(context["project"]),
                token="synthetic-ingestion-token-" + "x" * 32,
                identity=None,
            )
            monkeypatch.setattr(observer.exporter, "emit", emitted.append)
            try:
                extra = operation_headers(uuid4())
                kwargs = dict(
                    model="gpt-6-luna",
                    input="owned synthetic private phrase",
                    max_output_tokens=128,
                    store=False,
                    service_tier="default",
                    reasoning={"effort": "medium"},
                    extra_headers=extra,
                )
                if scenario == "success":
                    response = await invoke()
                    from openai.types.responses import Response

                    assert isinstance(response, Response) and response.output_text == "A"
                else:
                    from openai import APIStatusError

                    with pytest.raises(APIStatusError):
                        await invoke()
                assert len(upstream_calls) == 1
                assert len(front_calls) == (2 if scenario == "lost_ack" else 1)
                assert emitted == []
                assert observer.exporter.health().get("gateway_delegated") == 1
                with context["app"].state.sessions() as session:
                    call = session.get(
                        GatewayCallRow,
                        (context["project"], UUID(extra["X-Cutback-Operation-ID"])),
                    )
                    assert call.state == (
                        "SETTLED" if scenario in {"success", "lost_ack"} else "UNCERTAIN"
                    )
                    assert (
                        session.scalar(
                            select(func.count())
                            .select_from(StoredEvent)
                            .where(StoredEvent.project_id == context["project"])
                        )
                        == 1
                    )
                # Reusing the same logical ID is a conflict, even after successful
                # delivery. No response body is saved and no second provider call runs.
                from openai import APIStatusError

                with pytest.raises(APIStatusError) as conflict:
                    await invoke()
                assert conflict.value.status_code == 409
                assert len(upstream_calls) == 1
            finally:
                observer.close()
                closed = native.close()
                if asynchronous:
                    await closed

        asyncio.run(exercise())


def test_default_disabled_gateway_needs_no_database_or_provider_key():
    with TestClient(create_gateway(), raise_server_exceptions=False) as client:
        response = client.post("/v1/responses", json={"private": "not accepted"})
        assert response.status_code == 503
        assert response.headers["x-should-retry"] == "false"
        assert "private" not in response.text
