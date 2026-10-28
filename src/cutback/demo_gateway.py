"""Native application -> real loopback gateway -> PostgreSQL -> scoped report.

The inference transport is always synthetic. This example never reads a provider
key from the environment, contacts an inference service or deploys an application.
"""

import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4


@contextmanager
def loopback_server(app):
    import uvicorn

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    instance = uvicorn.Server(
        uvicorn.Config(app, access_log=False, log_level="error", timeout_graceful_shutdown=3)
    )
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not instance.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not instance.started:
            raise ValueError("SYNTHETIC_GATEWAY_START_UNCONFIRMED")
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if thread.is_alive():
            raise ValueError("SYNTHETIC_GATEWAY_STOP_UNCONFIRMED")


def native_fixture():
    return {
        "id": "resp_owned_gateway",
        "object": "response",
        "created_at": 1,
        "model": "gpt-6-luna",
        "service_tier": "default",
        "status": "completed",
        "output": [
            {
                "id": "msg_owned",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "A", "annotations": []}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
            "input_tokens_details": {
                "cached_tokens": 0,
                "cache_write_tokens": 0,
                "cache_write_tokens_details": {"ttl_30m_tokens": 0, "ttl_12h_tokens": 0},
            },
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }


def run_sample(app, endpoint, admin, project, *, client=None):
    from importlib.metadata import version

    import httpx
    from openai import APIStatusError, OpenAI
    from pydantic import SecretStr

    from cutback.agent_control import Control
    from cutback.core.native_models import NativeModelContract
    from cutback.core.sdk_compat import OPENAI_TEXT_SDKS
    from cutback.gateway.app import create_gateway
    from cutback.gateway.contracts import GatewayPolicy, ModelLimit, SpendLimit, TeamLimit
    from cutback.sample_app.gateway import classify

    if version("openai") not in OPENAI_TEXT_SDKS:
        raise ValueError("SYNTHETIC_GATEWAY_REQUIRES_QUALIFIED_RESPONSES_SDK")

    control = Control(endpoint, admin, client=client)
    base = f"/v1/projects/{project}"
    environment = control("GET", base + "/environments")["items"][0]["id"]
    now = datetime.now(UTC)
    project_limit = SpendLimit(modeled_usd=Decimal("5"), requests=10, concurrent_requests=4)
    shared_limit = SpendLimit(modeled_usd=Decimal("1.1"), requests=10, concurrent_requests=4)
    team = uuid4()
    specification = GatewayPolicy(
        policy_id=uuid4(),
        environment_ids=[UUID(environment)],
        starts_at=now - timedelta(seconds=1),
        expires_at=now + timedelta(hours=1),
        project_limit=project_limit,
        teams=[TeamLimit(team_id=team, limit=shared_limit)],
        models=[
            ModelLimit(
                price_contract=NativeModelContract.current_luna_text(reasoning_effort="medium"),
                limit=shared_limit,
            )
        ],
    )
    policy = control("POST", base + "/gateway/policies", specification.model_dump(mode="json"))
    keys = []
    calls = []

    async def provider(request):
        if request.url.host != "api.openai.com" or request.url.path != "/v1/responses":
            raise ValueError("SYNTHETIC_GATEWAY_PROVIDER_SCOPE_REFUSED")
        calls.append(1)
        result = native_fixture()
        if len(calls) == 2:
            result.pop("usage")
        return httpx.Response(200, json=result)

    gateway = create_gateway(
        database_url=app.state.engine.url.render_as_string(hide_password=False),
        enabled=True,
        provider_key=SecretStr("owned-synthetic-provider-key-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        allowed_project_ids=frozenset({UUID(str(project))}),
        transport=httpx.MockTransport(provider),
        allow_insecure_http=True,
    )
    try:
        policy = control(
            "POST",
            base + f"/gateway/policies/{policy['id']}/approve",
            {
                "approval_hash": policy["approval_hash"],
                "approval_ref": str(uuid4()),
                "expected_state_version": policy["state_version"],
            },
        )
        for _ in range(2):
            keys.append(
                control(
                    "POST",
                    base + "/gateway/keys",
                    {
                        "request_id": str(uuid4()),
                        "policy_id": policy["id"],
                        "environment_id": environment,
                        "team_id": str(team),
                        "expires_at": specification.expires_at.isoformat(),
                        "limit": shared_limit.model_dump(mode="json"),
                        "model_keys": [specification.models[0].key],
                    },
                )
            )
        with loopback_server(gateway) as gateway_endpoint:
            with OpenAI(
                api_key=keys[0]["token"],
                base_url=gateway_endpoint + "/v1",
                http_client=httpx.Client(trust_env=False, follow_redirects=False),
                timeout=5,
            ) as native:
                operation = uuid4()
                if classify(native, "owned independent classifier fixture", operation) != "A":
                    raise ValueError("SYNTHETIC_GATEWAY_NATIVE_OUTPUT_MISMATCH")
                try:
                    classify(native, "owned independent classifier fixture", operation)
                except APIStatusError as exc:
                    if exc.status_code != 409:
                        raise ValueError("SYNTHETIC_GATEWAY_DUPLICATE_NOT_BLOCKED") from None
                else:
                    raise ValueError("SYNTHETIC_GATEWAY_DUPLICATE_REPLAYED")
                classify(native, "owned usage-unavailable fixture", uuid4())
            # A new key cannot evade the exhausted shared team/model allowance.
            with OpenAI(
                api_key=keys[1]["token"],
                base_url=gateway_endpoint + "/v1",
                http_client=httpx.Client(trust_env=False, follow_redirects=False),
                timeout=5,
            ) as rotated:
                try:
                    classify(rotated, "owned shared-limit fixture", uuid4())
                except APIStatusError as exc:
                    if exc.status_code != 429:
                        raise ValueError("SYNTHETIC_GATEWAY_SHARED_LIMIT_NOT_ENFORCED") from None
                else:
                    raise ValueError("SYNTHETIC_GATEWAY_SHARED_LIMIT_BYPASSED")
        detail = control("GET", base + f"/gateway/policies/{policy['id']}")
        records = control("GET", base + "/gateway/calls")
        if len(calls) != 2 or sorted(row["state"] for row in records["items"]) != [
            "SETTLED",
            "UNCERTAIN",
        ]:
            raise ValueError("SYNTHETIC_GATEWAY_ACCOUNTING_INCOMPLETE")
        return {
            "project_id": str(project),
            "policy_id": policy["id"],
            "provider_fixture_requests": len(calls),
            "duplicate_blocked": True,
            "rotated_key_shared_limit_blocked": True,
            "budgets": detail["budgets"],
            "receipts": records["items"],
            "paid_provider_calls": 0,
            "dashboard": endpoint + "/dashboard/?project=" + str(project),
            "disclosure": (
                "Native SDK/HTTP/PostgreSQL are real; responses, usage and costs are synthetic "
                "fixtures, not customer quality or savings."
            ),
        }
    finally:
        failed = False
        for key in keys:
            try:
                control("DELETE", base + f"/gateway/keys/{key['key']['id']}")
            except ValueError:
                failed = True
        try:
            control(
                "POST",
                base + f"/gateway/policies/{policy['id']}/disable",
                {
                    "approval_hash": policy["approval_hash"],
                    "expected_state_version": policy["state_version"],
                },
            )
        except ValueError:
            failed = True
        if failed:
            raise ValueError("SYNTHETIC_GATEWAY_CONTROL_CLEANUP_UNCONFIRMED")
