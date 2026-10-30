import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from unit.test_guardrail_service import service
from unit.test_guardrails import policy

from cutback.core.guardrails import GuardrailBinding, GuardrailCheckReceipt
from cutback.guardrail_client import AsyncGuardrailClient, GuardrailClient, guardrail_origin
from cutback.guardrails import GuardrailEngine


def binding(definition=None):
    definition = definition or policy()
    return definition, GuardrailBinding(service_id=uuid4(), **definition.public_metadata())


def reply(definition, scope, request, *, overrides=None):
    body = json.loads(request.content)
    decision = GuardrailEngine(definition).check(
        operation_id=body["operation_id"],
        stage=body["stage"],
        content=body["content"],
        project_id=scope.project_id,
        principal_id=scope.principal_id,
        environment=scope.environment,
        workflow="owned-record-task",
    )
    raw = GuardrailCheckReceipt(
        service_id=scope.service_id,
        project_id=scope.project_id,
        principal_id=scope.principal_id,
        environment=scope.environment,
        workflow="owned-record-task",
        decision=decision,
    ).model_dump(mode="json")
    raw.update(overrides or {})
    return raw


def test_real_asgi_scope_and_check_receipts_sync_without_exporting_payloads():
    app, definition, _, path, _, headers = service()
    with TestClient(app, base_url="https://owned-guard.invalid") as server:
        metadata = server.get(path, headers=headers).json()
        scope = GuardrailBinding(service_id=metadata["service_id"], **metadata["policy"])
        requests = []

        def dispatch(request):
            requests.append(request)
            response = server.request(
                request.method,
                request.url.path,
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=dict(response.headers)
            )

        client = GuardrailClient(
            "https://owned-guard.invalid",
            headers["Authorization"][7:],
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(dispatch),
        )
        try:
            operation = uuid4()
            result = client.check(
                operation_id=operation, stage="input", content="Owned benign task"
            )
            assert result.status == "allowed" and result.coverage_complete
            denied = client.check(
                operation_id=uuid4(), stage="output", content="SYNTHETIC_DENIED_LITERAL_OWNED"
            )
            assert denied.status == "blocked" and denied.action == "deny"
            changed = client.check(operation_id=operation, stage="input", content="Changed task")
            assert changed.code == "GUARDRAIL_OPERATION_CONFLICT" and changed.action == "deny"
            assert len(requests) == 3 and all(
                request.headers["Accept-Encoding"] == "identity" for request in requests
            )
            assert "SYNTHETIC_DENIED" not in denied.model_dump_json()
        finally:
            client.close()
        assert client.check(operation_id=uuid4(), stage="input", content="Owned").action == "deny"
        assert len(requests) == 3


@pytest.mark.parametrize("mode,action", [("fail_open", "allow"), ("fail_closed", "deny")])
def test_sync_transport_outage_is_one_check_unknown_no_retry_and_no_sensitive_logging(
    caplog, mode, action
):
    definition, scope = binding(policy(on_unavailable=mode))
    requests = []
    logger = logging.getLogger("httpx.owned_guardrail_test")

    def outage(request):
        requests.append(request)
        logger.debug("SYNTHETIC_PRIVATE_LOG_DETAIL %s", request.content)
        raise httpx.ReadError("SYNTHETIC_PRIVATE_FAILURE_DETAIL")

    with caplog.at_level(logging.DEBUG):
        client = GuardrailClient(
            "https://owned-guard.invalid",
            "synthetic-token-" + "x" * 40,
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(outage),
        )
        try:
            result = client.check(
                operation_id=uuid4(), stage="input", content="SYNTHETIC_OWNED_REQUEST_CONTENT"
            )
        finally:
            client.close()
    assert len(requests) == 1 and result.status == "unavailable" and result.action == action
    assert result.duration_ms is None and not result.coverage_complete
    assert "SYNTHETIC_PRIVATE" not in caplog.text and "SYNTHETIC_OWNED_REQUEST" not in caplog.text
    assert "SYNTHETIC_PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404, 409, 413, 415, 422])
def test_authority_and_protocol_denials_cannot_inherit_fail_open(status):
    _, scope = binding(policy(on_unavailable="fail_open"))
    requests = []

    def deny(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://other.invalid"},
            json={"detail": "SYNTHETIC_PRIVATE_ERROR"},
        )

    client = GuardrailClient(
        "https://owned-guard.invalid",
        "x" * 48,
        binding=scope,
        workflow="owned-record-task",
        transport=httpx.MockTransport(deny),
    )
    try:
        result = client.check(operation_id=uuid4(), stage="input", content="Owned")
        assert len(requests) == 1 and result.action == "deny" and result.status == "blocked"
        assert "SYNTHETIC_PRIVATE" not in result.model_dump_json()
    finally:
        client.close()


def test_corrupt_scope_or_expected_coverage_is_not_a_verified_allow():
    definition, scope = binding(policy(on_unavailable="fail_open"))

    for corrupt in ("scope", "coverage"):

        def broken(request, corrupt=corrupt):
            value = reply(definition, scope, request)
            if corrupt == "scope":
                value["principal_id"] = str(uuid4())
            else:
                value["decision"].update(checks_expected=2, checks_completed=2)
            return httpx.Response(200, json=value)

        client = GuardrailClient(
            "https://owned-guard.invalid",
            "x" * 48,
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(broken),
        )
        try:
            result = client.check(operation_id=uuid4(), stage="input", content="Owned")
            assert result.status == "blocked" and result.action == "deny"
        finally:
            client.close()


def test_authority_expiring_during_failed_transport_remains_hard_denied(monkeypatch):
    definition, scope = binding(policy(on_unavailable="fail_open"))
    expired = False

    class Clock:
        @classmethod
        def now(cls, zone):
            return scope.expires_at + timedelta(seconds=1) if expired else datetime.now(UTC)

    def outage(request):
        nonlocal expired
        expired = True
        raise httpx.ReadError("SYNTHETIC_PRIVATE_FAILURE")

    monkeypatch.setattr("cutback.guardrail_client.datetime", Clock)
    client = GuardrailClient(
        "https://owned-guard.invalid",
        "x" * 48,
        binding=scope,
        workflow="owned-record-task",
        transport=httpx.MockTransport(outage),
    )
    try:
        result = client.check(operation_id=uuid4(), stage="input", content="Owned")
        assert result.status == "blocked" and result.code == "GUARDRAIL_EXPIRED"
    finally:
        client.close()


def test_async_check_preserves_decisions_and_closed_authority():
    definition, scope = binding()

    async def run():
        requests = []

        async def handle(request):
            requests.append(request)
            return httpx.Response(200, json=reply(definition, scope, request))

        client = AsyncGuardrailClient(
            "https://owned-guard.invalid",
            "x" * 48,
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(handle),
        )
        try:
            result = await client.check(operation_id=uuid4(), stage="input", content="Owned")
            assert result.status == "allowed" and len(requests) == 1
        finally:
            await client.close()
        assert (
            await client.check(operation_id=uuid4(), stage="input", content="Owned")
        ).action == "deny"

    asyncio.run(run())


@pytest.mark.parametrize("mode,action", [("fail_open", "allow"), ("fail_closed", "deny")])
def test_async_outage_is_unknown_once_and_cancellation_never_becomes_allow(mode, action):
    _, scope = binding(policy(on_unavailable=mode))

    async def run():
        calls = []

        async def outage(request):
            calls.append(request)
            raise httpx.ReadTimeout("SYNTHETIC_PRIVATE_ASYNC_DETAIL")

        client = AsyncGuardrailClient(
            "https://owned-guard.invalid",
            "x" * 48,
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(outage),
        )
        try:
            result = await client.check(operation_id=uuid4(), stage="input", content="Owned")
            assert len(calls) == 1 and result.status == "unavailable" and result.action == action
            assert not result.coverage_complete and result.duration_ms is None
            assert "SYNTHETIC_PRIVATE" not in result.model_dump_json()
        finally:
            await client.close()

        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def pending(request):
            calls.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        client = AsyncGuardrailClient(
            "https://owned-guard.invalid",
            "x" * 48,
            binding=scope,
            workflow="owned-record-task",
            transport=httpx.MockTransport(pending),
        )
        try:
            task = asyncio.create_task(
                client.check(operation_id=uuid4(), stage="input", content="Owned")
            )
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set() and len(calls) == 2
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://user:secret@example.invalid",
        "https://example.invalid/path",
        "https://example.invalid?query=value",
        "https://example.invalid#fragment",
        "https://example.invalid:0",
        "https://[",
        "https://example.invalid\n",
        None,
    ],
)
def test_origins_are_fixed_tls_and_never_take_credentials_paths_or_queries(origin):
    with pytest.raises(ValueError, match="FIXED_TLS_ORIGIN"):
        guardrail_origin(origin, allow_insecure_loopback=True)
    assert (
        guardrail_origin("http://127.0.0.1:8092", allow_insecure_loopback=True)
        == "http://127.0.0.1:8092"
    )
