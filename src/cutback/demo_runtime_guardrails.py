"""Independent native app -> separate checker -> gateway -> PostgreSQL receipts."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4


def seed_runtime_guardrails(app, control, project, *, headers=None):
    import httpx
    from openai import APIStatusError, OpenAI, Stream
    from openai.types.responses import Response, ResponseTextDeltaEvent
    from pydantic import SecretStr
    from sqlalchemy import select

    from cutback.core.guardrails import GuardrailPolicy
    from cutback.core.native_models import NativeModelContract
    from cutback.demo_gateway import loopback_server, native_fixture
    from cutback.gateway.app import create_gateway
    from cutback.gateway.client import operation_headers
    from cutback.gateway.contracts import GatewayPolicy, ModelLimit, SpendLimit, TeamLimit
    from cutback.sample_app.guardrail_service import owned_guardrail_service
    from cutback.server.gateway_db import GatewayCallRow

    project, principal, service_id = UUID(str(project)), uuid4(), uuid4()
    workflow, base = "owned-guarded-support", f"/v1/projects/{project}"

    def call(method, path, body=None):
        response = control.request(
            method,
            base + path,
            **({"headers": headers} if headers else {}),
            **({"json": body} if body is not None else {}),
        )
        if response.is_error:
            raise ValueError("OWNED_RUNTIME_GUARD_CONTROL_REFUSED_" + str(response.status_code))
        return response.json()

    now = datetime.now(UTC)
    capture = call(
        "POST",
        "/capture-policies",
        {
            "enabled": True,
            "approval_ref": "owned-runtime-guardrail-sample",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": [workflow],
            "allowed_providers": ["openai"],
            "max_samples": 4,
            "sample_rate": 1,
            "expires_at": (now + timedelta(days=1)).isoformat(),
        },
    )
    definition = GuardrailPolicy.model_validate(
        {
            "policy_id": str(uuid4()),
            "project_id": str(project),
            "principal_id": str(principal),
            "environment": "staging",
            "workflows": [workflow],
            "starts_at": now - timedelta(seconds=1),
            "expires_at": now + timedelta(minutes=15),
            "content_use_approved": True,
            "protected_stages": ["input", "output"],
            "rules": [
                {
                    "kind": "literal_deny",
                    "key": "owned-disclosure-boundary",
                    "stages": ["input", "output"],
                    "terms": ["SYNTHETIC_GUARDED_SAMPLE_CANARY"],
                }
            ],
        }
    )
    guard = call(
        "POST",
        "/runtime-guardrails",
        {
            "service_id": str(service_id),
            "capture_policy_id": capture["id"],
            "policy": definition.model_dump(mode="json"),
            "max_checks": 20,
        },
    )
    guard = call(
        "POST",
        f"/runtime-guardrails/{guard['id']}/approve",
        {
            "approval_hash": guard["approval_hash"],
            "approval_ref": str(uuid4()),
            "expected_state_version": guard["state_version"],
        },
    )
    environment = next(
        value["id"] for value in call("GET", "/environments")["items"] if value["name"] == "staging"
    )
    limit, team = SpendLimit(modeled_usd=Decimal("10"), requests=10, concurrent_requests=2), uuid4()
    policy, issued, calls, result = None, None, [], None

    async def provider(request):
        calls.append(1)
        body, response = json.loads(request.content), native_fixture()
        if body["input"] == "owned-output-deny":
            response["output"][0]["content"][0]["text"] = "SYNTHETIC_GUARDED_SAMPLE_CANARY"
        if body.get("stream"):
            values = (
                ["SYNTHETIC_GUARDED_", "SAMPLE_CANARY"]
                if body["input"] == "owned-split-deny"
                else ["A"]
            )
            wire = "".join(
                "data: "
                + json.dumps({"type": "response.output_text.delta", "delta": value})
                + "\n\n"
                for value in values
            )
            wire += (
                "data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n"
            )
            return httpx.Response(
                200, content=wire.encode(), headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=response)

    try:
        with owned_guardrail_service(
            definition, workflow=workflow, service_id=service_id
        ) as checker:
            specification = GatewayPolicy(
                version="cutback.gateway-policy.v3",
                policy_id=uuid4(),
                environment_ids=[UUID(environment)],
                starts_at=now,
                expires_at=definition.expires_at,
                project_limit=limit,
                teams=[TeamLimit(team_id=team, limit=limit)],
                models=[
                    ModelLimit(
                        key="openai-luna-responses-text-stream-v1",
                        price_contract=NativeModelContract.current_luna_text(
                            reasoning_effort="medium"
                        ),
                        limit=limit,
                    )
                ],
                runtime_guardrails=checker.binding,
            )
            policy = call("POST", "/gateway/policies", specification.model_dump(mode="json"))
            policy = call(
                "POST",
                f"/gateway/policies/{policy['id']}/approve",
                {
                    "approval_hash": policy["approval_hash"],
                    "approval_ref": str(uuid4()),
                    "expected_state_version": policy["state_version"],
                },
            )
            issued = call(
                "POST",
                "/gateway/keys",
                {
                    "request_id": str(uuid4()),
                    "policy_id": policy["id"],
                    "environment_id": environment,
                    "team_id": str(team),
                    "expires_at": definition.expires_at.isoformat(),
                    "limit": limit.model_dump(mode="json"),
                    "model_keys": [specification.models[0].key],
                },
            )
            gateway = create_gateway(
                enabled=True,
                database_url=app.state.engine.url.render_as_string(hide_password=False),
                provider_key=SecretStr("owned-synthetic-provider-key-only"),
                hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
                allowed_project_ids=frozenset({project}),
                transport=httpx.MockTransport(provider),
                allow_insecure_http=True,
                guardrail_clients={definition.policy_id: checker.async_client()},
            )
            outcomes = {}
            with (
                loopback_server(gateway) as endpoint,
                OpenAI(
                    base_url=endpoint + "/v1",
                    api_key=issued["token"],
                    max_retries=0,
                    http_client=httpx.Client(trust_env=False, follow_redirects=False),
                    timeout=10,
                ) as native,
            ):
                for name, text, streaming, permitted in (
                    ("benign", "owned-benign", False, True),
                    ("input_deny", "SYNTHETIC_GUARDED_SAMPLE_CANARY", False, False),
                    ("output_deny", "owned-output-deny", False, False),
                    ("split_stream_deny", "owned-split-deny", True, False),
                    ("benign_stream", "owned-benign-stream", True, True),
                ):
                    operation = uuid4()
                    try:
                        value = native.responses.create(
                            model="gpt-6-luna",
                            input=text,
                            store=False,
                            stream=streaming,
                            service_tier="default",
                            max_output_tokens=128,
                            extra_headers=operation_headers(operation),
                        )
                        if streaming:
                            if not isinstance(value, Stream):
                                raise ValueError("OWNED_RUNTIME_GUARD_NATIVE_STREAM_TYPE_INVALID")
                            with value:
                                answer = "".join(
                                    event.delta
                                    for event in value
                                    if isinstance(event, ResponseTextDeltaEvent)
                                )
                        else:
                            if not isinstance(value, Response):
                                raise ValueError("OWNED_RUNTIME_GUARD_NATIVE_RESPONSE_TYPE_INVALID")
                            answer = value.output_text
                        if not permitted or answer != "A":
                            raise ValueError("OWNED_RUNTIME_GUARD_SAMPLE_DELIVERY_INVALID")
                        outcomes[name] = {"delivered": True, "operation_id": str(operation)}
                    except APIStatusError as error:
                        if permitted or error.status_code != 403:
                            raise ValueError("OWNED_RUNTIME_GUARD_SAMPLE_DENIAL_INVALID") from None
                        outcomes[name] = {"delivered": False, "operation_id": str(operation)}
            with app.state.sessions() as session:
                rows = session.scalars(
                    select(GatewayCallRow).where(
                        GatewayCallRow.project_id == project,
                        GatewayCallRow.policy_id == UUID(policy["id"]),
                    )
                ).all()
                if (
                    len(calls) != 4
                    or len(rows) != 4
                    or any(row.state != "SETTLED" or row.cost_usd is None for row in rows)
                ):
                    raise ValueError("OWNED_RUNTIME_GUARD_SAMPLE_ACCOUNTING_INCOMPLETE")
                known = sum((row.cost_usd for row in rows), Decimal(0))
            result = {
                "status": "PASSED",
                "policy_id": guard["id"],
                "gateway_policy_id": policy["id"],
                "native_fixture_calls": len(calls),
                "paid_provider_calls": 0,
                "known_modeled_native_usd": str(known),
                "complete_application_cost_usd": None,
                "outcomes": outcomes,
                "separate_checker_process": True,
            }
    finally:
        failed = False
        for method, path, body in (
            ("DELETE", f"/gateway/keys/{issued['key']['id']}", None)
            if issued
            else (None, None, None),
            (
                "POST",
                f"/gateway/policies/{policy['id']}/disable",
                {
                    "approval_hash": policy["approval_hash"],
                    "expected_state_version": policy["state_version"],
                },
            )
            if policy
            else (None, None, None),
            (
                "POST",
                f"/runtime-guardrails/{guard['id']}/disable",
                {
                    "approval_hash": guard["approval_hash"],
                    "expected_state_version": guard["state_version"],
                },
            ),
        ):
            if method:
                try:
                    call(method, path, body)
                except Exception:
                    failed = True
        if failed:
            raise ValueError("OWNED_RUNTIME_GUARD_SAMPLE_CONTROL_CLEANUP_UNCONFIRMED")
    return result
