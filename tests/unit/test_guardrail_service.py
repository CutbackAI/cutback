import asyncio
import hashlib
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from unit.test_guardrails import policy

from cutback.guardrail_service import GuardrailClientAuthority, create_guardrail_service
from cutback.guardrails import GuardrailEngine


def service(*, max_retained=1000, max_inflight=8, checks_per_minute=600, evaluation_ms=1000):
    # HTTP identity/replay tests must not depend on the host scheduling a worker
    # inside the production default's 50ms. Deadline behavior has its own test.
    definition = policy(max_evaluation_ms=evaluation_ms)
    token = secrets.token_urlsafe(48)
    authority = GuardrailClientAuthority(
        project_id=definition.project_id,
        principal_id=definition.principal_id,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        policy_ids=(definition.policy_id,),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    app = create_guardrail_service(
        service_id=uuid4(),
        policies=[definition],
        clients=[authority],
        max_retained=max_retained,
        max_inflight=max_inflight,
        checks_per_minute=checks_per_minute,
    )
    path = f"/v1/projects/{definition.project_id}/guardrails/{definition.policy_id}"
    body = {
        "operation_id": str(uuid4()),
        "policy_sha256": definition.policy_sha256,
        "environment": definition.environment,
        "workflow": "owned-record-task",
        "stage": "input",
        "content": "Owned benign request",
    }
    return app, definition, authority, path, body, {"Authorization": "Bearer " + token}


def test_actual_http_policy_scope_bound_replay_and_metadata_without_content():
    app, definition, authority, path, body, headers = service(max_retained=1)
    with TestClient(app, base_url="https://owned-guard.invalid") as client:
        assert client.post(path + "/check", json=body).status_code == 403
        metadata = client.get(path, headers=headers)
        assert metadata.status_code == 200
        assert "content_nonce" not in metadata.text and "SYNTHETIC_DENIED" not in metadata.text
        response = client.post(path + "/check", json=body, headers=headers)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["decision"]["status"] == "allowed"
        assert receipt["principal_id"] == str(authority.principal_id)
        assert body["content"] not in response.text
        assert client.post(path + "/check", json=body, headers=headers).json() == receipt
        conflict = client.post(
            path + "/check", json={**body, "content": "Different owned request"}, headers=headers
        )
        assert conflict.status_code == 409 and "Different owned" not in conflict.text
        assert (
            client.get(path + "/checks/" + body["operation_id"] + "/input", headers=headers).json()
            == receipt
        )
        denied = client.post(
            path + "/check",
            json={
                **body,
                "operation_id": str(uuid4()),
                "content": "SYNTHETIC_DENIED_LITERAL_OWNED",
            },
            headers=headers,
        )
        assert denied.status_code == 200 and denied.json()["decision"]["status"] == "blocked"
        assert "SYNTHETIC_DENIED" not in denied.text
        assert (
            client.get(
                path + "/checks/" + body["operation_id"] + "/input", headers=headers
            ).status_code
            == 404
        )
        for change in (
            {"environment": "other"},
            {"workflow": "other"},
            {"policy_sha256": "a" * 64},
        ):
            assert (
                client.post(path + "/check", json={**body, **change}, headers=headers).status_code
                == 403
            )
        assert (
            client.post(
                path.replace(str(definition.project_id), str(uuid4())) + "/check",
                json=body,
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.post(
                path + "/check", json={**body, "principal_id": str(uuid4())}, headers=headers
            ).status_code
            == 422
        )
        invalid = client.post(
            path + "/check",
            content='{"content":"PRIVATE_REQUEST_DETAIL","content":"duplicate"}',
            headers={**headers, "Content-Type": "application/json"},
        )
        assert invalid.status_code == 422 and "PRIVATE_REQUEST_DETAIL" not in invalid.text
        invalid = client.get(
            path.replace(str(definition.project_id), "PRIVATE_PATH_DETAIL"), headers=headers
        )
        assert invalid.status_code == 422 and "PRIVATE_PATH_DETAIL" not in invalid.text


def test_plaintext_or_expired_policy_never_becomes_success_via_host_header():
    app, definition, _, path, body, headers = service()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post(path + "/check", json=body, headers=headers).status_code == 400
    with TestClient(app, base_url="https://owned-guard.invalid") as client:
        app.state.revoked_policy_ids.add(definition.policy_id)
        assert client.post(path + "/check", json=body, headers=headers).status_code == 403


def test_late_revocation_denies_inflight_result_and_cached_lookup(monkeypatch):
    app, _, authority, path, body, headers = service()
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = GuardrailEngine.check

    def slow(engine, **kwargs):
        calls.append(kwargs["operation_id"])
        entered.set()
        assert release.wait(5)
        return original(engine, **kwargs)

    monkeypatch.setattr(GuardrailEngine, "check", slow)
    with (
        TestClient(app, base_url="https://owned-guard.invalid") as client,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        first = pool.submit(client.post, path + "/check", json=body, headers=headers)
        assert entered.wait(5)
        second = pool.submit(client.post, path + "/check", json=body, headers=headers)
        try:
            app.state.revoked_client_hashes.add(authority.token_sha256)
        finally:
            release.set()
        assert first.result(timeout=10).status_code == 403
        assert second.result(timeout=10).status_code == 403
        assert len(calls) == 1
        assert (
            client.get(
                path + "/checks/" + body["operation_id"] + "/input", headers=headers
            ).status_code
            == 403
        )


def test_http_evaluation_deadline_fails_closed_and_replays_the_same_unavailable_receipt(
    monkeypatch,
):
    import cutback.guardrails as implementation

    app, _, _, path, body, headers = service(evaluation_ms=1)
    bound = implementation.bounded_episode_value
    calls = []

    def slow_bound(*args, **kwargs):
        calls.append(True)
        time.sleep(0.02)
        return bound(*args, **kwargs)

    monkeypatch.setattr(implementation, "bounded_episode_value", slow_bound)
    with TestClient(app, base_url="https://owned-guard.invalid") as client:
        response = client.post(path + "/check", json=body, headers=headers)
        assert response.status_code == 200
        receipt = response.json()
        assert receipt["decision"]["status"] == "unavailable"
        assert receipt["decision"]["action"] == "deny"
        assert not receipt["decision"]["coverage_complete"]
        assert body["content"] not in response.text
        assert client.post(path + "/check", json=body, headers=headers).json() == receipt
        assert calls == [True]


def test_process_rate_limit_and_body_caps_are_explicit_and_do_not_echo_inputs():
    app, _, _, path, body, headers = service(checks_per_minute=1)
    with TestClient(app, base_url="https://owned-guard.invalid") as client:
        assert client.post(path + "/check", json=body, headers=headers).status_code == 200
        result = client.post(path + "/check", json=body, headers=headers)
        assert result.status_code == 429 and body["content"] not in result.text
    app, _, _, path, body, headers = service()
    with TestClient(app, base_url="https://owned-guard.invalid") as client:
        result = client.post(
            path + "/check",
            content="SYNTHETIC_PRIVATE" * 100000,
            headers={**headers, "Content-Type": "application/json"},
        )
        assert result.status_code == 413 and "SYNTHETIC_PRIVATE" not in result.text


def test_client_cannot_be_registered_against_a_different_policy_owner():
    definition = policy()
    authority = GuardrailClientAuthority(
        project_id=definition.project_id,
        principal_id=uuid4(),
        token_sha256="a" * 64,
        policy_ids=(definition.policy_id,),
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    with pytest.raises(ValueError, match="CLIENT_POLICY_SCOPE"):
        create_guardrail_service(service_id=uuid4(), policies=[definition], clients=[authority])
    assert json.dumps(definition.public_metadata()).find("SYNTHETIC_DENIED") == -1


def test_cancelled_http_waiter_keeps_cpu_slot_and_duplicate_reuses_the_original(monkeypatch):
    app, _, _, path, body, headers = service(max_inflight=1)
    entered, release = threading.Event(), threading.Event()
    calls = []
    original = GuardrailEngine.check

    def slow(engine, **kwargs):
        calls.append(kwargs["operation_id"])
        entered.set()
        assert release.wait(5)
        return original(engine, **kwargs)

    monkeypatch.setattr(GuardrailEngine, "check", slow)

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://owned-guard.invalid"
            ) as client,
        ):
            first = asyncio.create_task(client.post(path + "/check", json=body, headers=headers))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                # Cancelling the HTTP waiter cannot release the ongoing CPU slot.
                full = await client.post(
                    path + "/check",
                    json={**body, "operation_id": str(uuid4())},
                    headers=headers,
                )
                assert full.status_code == 503
                duplicate = asyncio.create_task(
                    client.post(path + "/check", json=body, headers=headers)
                )
                await asyncio.sleep(0)
                assert not duplicate.done()
                release.set()
                result = await asyncio.wait_for(duplicate, 5)
                assert result.status_code == 200 and len(calls) == 1
                replay = await client.post(path + "/check", json=body, headers=headers)
                assert replay.json() == result.json()
                conflict = await client.post(
                    path + "/check", json={**body, "content": "changed"}, headers=headers
                )
                assert conflict.status_code == 409 and len(calls) == 1
            finally:
                release.set()

    asyncio.run(run())
