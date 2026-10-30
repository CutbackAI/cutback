"""Actual checker process, gateway/native HTTP, PostgreSQL and authenticated browser."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from integration.test_runtime_guardrail_controls import prepare
from pydantic import SecretStr
from support_browser import browser_command
from support_gateway import native_response
from test_pipeline import server

from cutback.gateway.app import create_gateway
from cutback.sample_app.guardrail_service import owned_guardrail_service


@pytest.mark.postgres
def test_guardrail_approval_native_delivery_cost_and_viewer_dashboard(
    api, tenant, tmp_path, monkeypatch
):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for actual authenticated guardrail acceptance")
    service_id = uuid4()
    definition, _, state, _, _ = prepare(api, tenant, tmp_path, monkeypatch, service_id=service_id)
    calls = []

    async def provider(request):
        calls.append(1)
        body = native_response()
        if len(calls) == 2:
            body["output"][0]["content"][0]["text"] = "SYNTHETIC_DENIED_LITERAL_OWNED"
        return httpx.Response(200, json=body)

    with owned_guardrail_service(
        definition, workflow="owned-record-task", service_id=service_id
    ) as checker:
        gateway = create_gateway(
            enabled=True,
            database_url=api[2].state.engine.url.render_as_string(hide_password=False),
            provider_key=SecretStr("owned-synthetic-provider-key-only"),
            hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
            transport=httpx.MockTransport(provider),
            allow_insecure_http=True,
            allowed_project_ids=frozenset({definition.project_id}),
            guardrail_clients={definition.policy_id: checker.async_client()},
        )
        token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
        with server(api[2]) as endpoint, server(gateway) as gateway_endpoint:
            completed = subprocess.run(
                browser_command(Path(__file__).resolve().parents[2] / "web", "guardrails.spec.ts"),
                cwd=Path(__file__).resolve().parents[2] / "web",
                env={
                    **os.environ,
                    "CUTBACK_UI_URL": endpoint,
                    "CUTBACK_UI_PROJECT": tenant[0],
                    "CUTBACK_UI_KEY": token,
                    "CUTBACK_GUARDRAIL_BROWSER": "true",
                    "CUTBACK_GUARDRAIL_POLICY": state["id"],
                    "CUTBACK_GUARDRAIL_VIEWER": viewer,
                    "CUTBACK_GATEWAY_UI_URL": gateway_endpoint,
                },
                capture_output=True,
                text=True,
                timeout=180,
            )
        output = (
            (completed.stdout + completed.stderr)
            .replace(token, "<credential-omitted>")
            .replace(viewer, "<credential-omitted>")
        )
        assert completed.returncode == 0, output
        assert calls == [1, 1]
