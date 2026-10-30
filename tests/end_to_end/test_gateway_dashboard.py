"""Authenticated gateway controls over real HTTP/PostgreSQL; provider calls are intercepted."""

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from support_browser import browser_command
from support_gateway import native_response
from test_pipeline import server

from cutback.gateway.app import create_gateway


@pytest.mark.postgres
def test_gateway_controls_dashboard(api, tenant):
    if os.getenv("CUTBACK_TEST_BROWSER") != "true":
        pytest.skip("set CUTBACK_TEST_BROWSER=true for authenticated gateway browser acceptance")
    calls = []

    async def provider(request):
        assert request.url.host == "api.openai.com" and request.url.path == "/v1/responses"
        calls.append(1)
        body = native_response()
        if len(calls) == 2:
            body.pop("usage")
        assert json.loads(request.content)["stream"] is True
        frames = (
            'data: {"type":"response.output_text.delta","delta":"A"}\n\n'
            + "event: response.completed\ndata: "
            + json.dumps({"type": "response.completed", "response": body})
            + "\n\n"
        )
        return httpx.Response(
            200, content=frames.encode(), headers={"content-type": "text/event-stream"}
        )

    gateway = create_gateway(
        database_url=api[2].state.engine.url.render_as_string(hide_password=False),
        enabled=True,
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
        allowed_project_ids=frozenset({UUID(tenant[0])}),
    )
    token, viewer = api[1]["Authorization"][7:], tenant[2]["Authorization"][7:]
    with server(api[2]) as endpoint, server(gateway) as gateway_endpoint:
        completed = subprocess.run(
            browser_command(Path(__file__).resolve().parents[2] / "web", "gateway.spec.ts"),
            cwd=Path(__file__).resolve().parents[2] / "web",
            env={
                **os.environ,
                "CUTBACK_UI_URL": endpoint,
                "CUTBACK_UI_KEY": token,
                "CUTBACK_UI_PROJECT": tenant[0],
                "CUTBACK_GATEWAY_BROWSER": "true",
                "CUTBACK_GATEWAY_VIEWER": viewer,
                "CUTBACK_GATEWAY_UI_URL": gateway_endpoint,
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    output = (completed.stdout + completed.stderr).replace(token, "<credential-omitted>")
    output = output.replace(viewer, "<credential-omitted>")
    assert completed.returncode == 0, output
    assert len(calls) == 2
