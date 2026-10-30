"""Guarded native gateway: intent-before-I/O, no early SSE, known cost survives denial."""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from end_to_end.test_pipeline import server
from fastapi.testclient import TestClient
from integration.test_runtime_guardrail_controls import approve, prepare
from pydantic import SecretStr
from sqlalchemy import func, select
from support_gateway import gateway_request, issue_gateway_key, native_response, prepare_gateway
from unit.test_guardrail_client import reply

from cutback.core.guardrails import GuardrailBinding
from cutback.gateway.app import create_gateway
from cutback.gateway.client import operation_headers
from cutback.guardrail_client import AsyncGuardrailClient
from cutback.server.db import StoredEvent
from cutback.server.gateway_db import GatewayCallRow
from cutback.server.guardrail_db import GuardrailOperationRow

pytestmark = pytest.mark.postgres


def context_for(
    api, tenant, tmp_path, monkeypatch, *, streaming=False, fail_mode="fail_closed", outage=False
):
    definition, _, state, path, _ = prepare(api, tenant, tmp_path, monkeypatch)
    if fail_mode != "fail_closed":
        # The fail-open choice is part of the immutable reviewed content/hash.
        from cutback.core.guardrails import GuardrailPolicy

        definition = GuardrailPolicy.model_validate(
            {
                **definition.model_dump(mode="json"),
                "policy_id": str(uuid4()),
                "on_unavailable": fail_mode,
            }
        )
        registration = api[0].get(path + f"/{state['id']}", headers=api[1]).json()
        response = api[0].post(
            path,
            headers=api[1],
            json={
                "service_id": str(uuid4()),
                "capture_policy_id": registration["capture_policy_id"],
                "policy": definition.model_dump(mode="json"),
                "max_checks": 10,
            },
        )
        assert response.status_code == 201, response.text
        state = response.json()
    state, _ = approve(api, state, path)
    binding = GuardrailBinding.model_validate(state["binding"])
    context = prepare_gateway(api, tenant, streaming=streaming, guard_binding=binding)
    key = issue_gateway_key(context)
    checks = []

    def check(request):
        checks.append(json.loads(request.content)["stage"])
        if outage:
            raise httpx.ReadError("PRIVATE_GUARD_TRANSPORT_DETAIL")
        return httpx.Response(200, json=reply(definition, binding, request))

    guard = AsyncGuardrailClient(
        "https://owned-guard.invalid",
        "x" * 48,
        binding=binding,
        workflow="owned-record-task",
        transport=httpx.MockTransport(check),
    )
    return context, key, guard, checks, path, state


def app_for(context, guard, provider):
    return create_gateway(
        enabled=True,
        database_url=context["app"].state.engine.url.render_as_string(hide_password=False),
        provider_key=SecretStr("owned-synthetic-provider-only"),
        hmac_key=SecretStr("owned-synthetic-hmac-key-" + "x" * 32),
        allowed_project_ids=frozenset({context["project"]}),
        allow_insecure_http=True,
        transport=httpx.MockTransport(provider),
        guardrail_clients={guard.binding.policy_id: guard},
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "allow",
        "input_deny",
        "output_deny",
        "hidden_deny",
        "split_parts_deny",
        "lost_settlement_ack",
        "revoke_after_charge",
        "outage_closed",
        "outage_open",
    ],
)
def test_durable_input_and_output_enforcement_preserves_actual_charge_and_private_boundary(
    api, tenant, tmp_path, monkeypatch, scenario
):
    context, key, guard, checks, path, state = context_for(
        api,
        tenant,
        tmp_path,
        monkeypatch,
        fail_mode="fail_open" if scenario == "outage_open" else "fail_closed",
        outage=scenario in {"outage_closed", "outage_open"},
    )
    operation, calls = uuid4(), []
    body = gateway_request().model_dump(mode="json")
    if scenario == "input_deny":
        body["input"] = "SYNTHETIC_DENIED_LITERAL_OWNED"
    if scenario == "lost_settlement_ack":
        from cutback.server import gateway_service

        original, acknowledgements = gateway_service.finish, []

        def lost(*args, **kwargs):
            result = original(*args, **kwargs)
            if not acknowledgements:
                acknowledgements.append(1)
                raise OSError("SYNTHETIC_LOST_SETTLEMENT_ACK_PRIVATE_DETAIL")
            return result

        monkeypatch.setattr(gateway_service, "finish", lost)

    async def provider(request):
        calls.append(1)
        with api[2].state.sessions() as session:
            guard_row = session.get(
                GuardrailOperationRow,
                (context["project"], guard.binding.policy_id, operation, "input"),
            )
            assert guard_row is not None and guard_row.decision["action"] == "allow"
        response = native_response()
        if scenario == "output_deny":
            response["output"][0]["content"][0]["text"] = "SYNTHETIC_DENIED_LITERAL_OWNED"
        if scenario == "hidden_deny":
            response["untrusted_metadata"] = "SYNTHETIC_DENIED_LITERAL_OWNED"
        if scenario == "split_parts_deny":
            response["output"][0]["content"] = [
                {"type": "output_text", "text": value, "annotations": []}
                for value in ["SYNTHETIC_DENIED_", "LITERAL_OWNED"]
            ]
        if scenario == "revoke_after_charge":
            revoked = api[0].post(
                path + f"/{state['id']}/disable",
                headers=api[1],
                json={"approval_hash": state["approval_hash"], "expected_state_version": 1},
            )
            assert revoked.status_code == 200
        return httpx.Response(200, json=response)

    with TestClient(app_for(context, guard, provider)) as client:
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key["token"], **operation_headers(operation)},
            json=body,
        )
        success = scenario in {"allow", "outage_open"}
        assert response.status_code == (
            200 if success else 503 if scenario == "lost_settlement_ack" else 403
        ), response.text
        assert "SYNTHETIC_DENIED_LITERAL_OWNED" not in response.text
        if success:
            assert response.headers["x-cutback-guardrail-coverage"] == (
                "incomplete" if scenario == "outage_open" else "complete"
            )
        # Same UUID cannot repeat a native call even after known output denial.
        replay = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key["token"], **operation_headers(operation)},
            json=body,
        )
        assert replay.status_code == 403
    with api[2].state.sessions() as session:
        call = session.get(GatewayCallRow, (context["project"], operation))
        if scenario in {"input_deny", "outage_closed"}:
            assert calls == [] and call is None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(StoredEvent)
                    .where(StoredEvent.project_id == context["project"])
                )
                == 0
            )
        else:
            assert calls == [1] and call.state == "SETTLED" and call.cost_usd > 0
        exported = api[0].get(f"/v1/projects/{context['project']}/export", headers=api[1])
        assert "SYNTHETIC_DENIED_LITERAL_OWNED" not in exported.text


@pytest.mark.parametrize(
    "scenario", ["allow", "split_deny", "refusal_split_deny", "wire_deny", "interrupted"]
)
def test_sse_is_fully_quarantined_including_split_deltas_and_raw_comment_fields(
    api, tenant, tmp_path, monkeypatch, scenario
):
    context, key, guard, checks, _, _ = context_for(
        api, tenant, tmp_path, monkeypatch, streaming=True
    )
    operation, calls, chunks = uuid4(), [], []

    def frame(kind, **fields):
        return (
            "event: " + kind + "\ndata: " + json.dumps({"type": kind, **fields}) + "\n\n"
        ).encode()

    class Source(httpx.AsyncByteStream):
        async def __aiter__(self):
            values = (
                ["SYNTHETIC_DENIED_", "LITERAL_OWNED"]
                if scenario in {"split_deny", "refusal_split_deny"}
                else ["A"]
            )
            for value in values:
                chunk = frame(
                    "response.refusal.delta"
                    if scenario == "refusal_split_deny"
                    else "response.output_text.delta",
                    delta=value,
                )
                chunks.append(chunk)
                yield chunk
            if scenario == "wire_deny":
                chunk = b": SYNTHETIC_DENIED_LITERAL_OWNED\n\n"
                chunks.append(chunk)
                yield chunk
            if scenario == "interrupted":
                raise httpx.ReadError("PRIVATE_UPSTREAM_INTERRUPTION")
            chunk = frame("response.completed", response=native_response())
            chunks.append(chunk)
            yield chunk

    async def provider(request):
        calls.append(1)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Source())

    body = {**gateway_request().model_dump(mode="json"), "stream": True}
    with TestClient(app_for(context, guard, provider)) as client:
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key["token"], **operation_headers(operation)},
            json=body,
        )
        expected = 200 if scenario == "allow" else 503 if scenario == "interrupted" else 403
        assert response.status_code == expected, response.text
        if scenario == "allow":
            assert response.content == b"".join(chunks)
            assert response.headers["x-cutback-output-buffering"] == "verified-before-delivery"
        else:
            assert "response.output_text.delta" not in response.text
            assert (
                "SYNTHETIC_DENIED" not in response.text and "PRIVATE_UPSTREAM" not in response.text
            )
    with api[2].state.sessions() as session:
        call = session.get(GatewayCallRow, (context["project"], operation))
        assert calls == [1] and call.first_text_delta_us is None
        if scenario == "interrupted":
            assert call.state == "UNCERTAIN" and call.cost_usd is None
        else:
            assert call.state == "SETTLED" and call.cost_usd > 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
def test_actual_native_sync_async_response_and_stream_clients_keep_native_types(
    api, tenant, tmp_path, monkeypatch, asynchronous, streaming
):
    from openai import AsyncOpenAI, OpenAI
    from openai.types.responses import Response, ResponseTextDeltaEvent

    # The SSE-qualified policy also permits nonstreaming calls under its same
    # frozen allowance, preserving the existing qualified gateway contract.
    context, key, guard, checks, _, _ = context_for(
        api, tenant, tmp_path, monkeypatch, streaming=True
    )
    calls = []

    class Source(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                b"event: response.output_text.delta\n"
                b'data: {"type":"response.output_text.delta","delta":"A"}\n\n'
            )
            await asyncio.sleep(0.03)
            yield (
                "event: response.completed\ndata: "
                + json.dumps({"type": "response.completed", "response": native_response()})
                + "\n\n"
            ).encode()

    async def provider(request):
        calls.append(1)
        if json.loads(request.content).get("stream"):
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=Source()
            )
        return httpx.Response(200, json=native_response())

    operation = uuid4()
    with server(app_for(context, guard, provider)) as endpoint:
        options = {
            "model": "gpt-6-luna",
            "input": "Owned synthetic question",
            "stream": streaming,
            "store": False,
            "service_tier": "default",
            "max_output_tokens": 1024,
            "extra_headers": operation_headers(operation),
        }
        if asynchronous:

            async def run():
                async with AsyncOpenAI(
                    base_url=endpoint + "/v1",
                    api_key=key["token"],
                    max_retries=0,
                    http_client=httpx.AsyncClient(trust_env=False),
                ) as native:
                    result = await native.responses.create(**options)
                    if streaming:
                        deltas = []
                        async with result:
                            async for event in result:
                                assert "output" in checks
                                if isinstance(event, ResponseTextDeltaEvent):
                                    deltas.append(event.delta)
                        assert deltas == ["A"]
                    else:
                        assert isinstance(result, Response) and result.output_text == "A"

            asyncio.run(run())
        else:
            with OpenAI(
                base_url=endpoint + "/v1",
                api_key=key["token"],
                max_retries=0,
                http_client=httpx.Client(trust_env=False),
            ) as native:
                result = native.responses.create(**options)
                if streaming:
                    deltas = []
                    with result:
                        for event in result:
                            assert "output" in checks
                            if isinstance(event, ResponseTextDeltaEvent):
                                deltas.append(event.delta)
                    assert deltas == ["A"]
                else:
                    assert isinstance(result, Response) and result.output_text == "A"
    assert calls == [1] and checks == ["input", "output"]
