"""Separate service authority/idempotency; these tests do not assert OS isolation."""

import hashlib
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from unit.test_isolated_records import records

from cutback.isolated_records import IsolatedRecords
from cutback.tool_broker import BrokerClient, create_tool_broker


class LocalTestRuntime:
    starts = 0
    calls = 0
    lose_ack = False

    def __init__(self, policy, seeds, *, endpoint, on_prepared, broker_id=None):
        type(self).starts += 1
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.engine = self.pool.submit(IsolatedRecords, policy, seeds, b"x" * 32).result()
        self.cleaned = False
        on_prepared(self)

    def execute(self, operation, tool, arguments):
        type(self).calls += 1
        result = self.pool.submit(self.engine.execute, operation, tool, arguments).result()
        if type(self).lose_ack:
            raise ConnectionError("synthetic lost tool acknowledgement")
        return result

    def owner_snapshot(self):
        return self.pool.submit(self.engine.owner_snapshot).result()

    def containment_receipt(self):
        return {"cleanup_confirmed": self.cleaned}

    def close(self, *, reconcile=False):
        if not self.cleaned:
            self.pool.submit(self.engine.close).result()
            self.pool.shutdown(wait=True)
            self.cleaned = True


def setup(monkeypatch, *, maximum=4):
    scratch, policy, own, outsider, other_owner = records()
    scratch.close()
    token, other_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    clients = [
        BrokerClient(
            project_id=policy.project_id,
            principal_id=policy.principal_id,
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        BrokerClient(
            project_id=uuid4(),
            principal_id=uuid4(),
            token_sha256=hashlib.sha256(other_token.encode()).hexdigest(),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    ]
    monkeypatch.setattr(LocalTestRuntime, "starts", 0)
    monkeypatch.setattr(LocalTestRuntime, "calls", 0)
    monkeypatch.setattr(LocalTestRuntime, "lose_ack", False)
    app = create_tool_broker(
        image_id=policy.image_id,
        endpoint="npipe:////./pipe/synthetic-test-only",
        clients=clients,
        max_active_sessions=maximum,
        runtime_factory=LocalTestRuntime,
    )
    headers = {"Authorization": "Bearer " + token}
    other_headers = {"Authorization": "Bearer " + other_token}
    body = {
        "broker_generation": str(app.state.generation),
        "policy": policy.model_dump(mode="json"),
        "seeds": [item.model_dump(mode="json") for item in (own, outsider, other_owner)],
        "approval_hash": "a" * 64,
    }
    return app, headers, other_headers, policy, own, body


def test_project_principal_content_boundaries_and_same_start_uuid_never_restart(monkeypatch):
    app, headers, other, policy, own, body = setup(monkeypatch)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with TestClient(app, base_url="https://owned-broker.invalid") as client:
        assert client.post(path, headers=other, json=body).status_code == 403
        assert (
            client.post(
                path, headers=headers, json={**body, "broker_generation": str(uuid4())}
            ).status_code
            == 403
        )
        assert (
            client.post(
                path,
                headers=headers,
                json={**body, "policy": {**body["policy"], "environment": "production"}},
            ).status_code
            == 403
        )
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == 202 and response.json()["state"] == "READY", response.text
        assert own.fields["private_note"] not in response.text
        assert client.post(path, headers=headers, json=body).status_code == 202
        assert LocalTestRuntime.starts == 1
        assert (
            client.post(path, headers=headers, json={**body, "approval_hash": "b" * 64}).status_code
            == 409
        )
        route = path + "/" + str(policy.session_id)
        assert client.get(route, headers=other).status_code == 404
        operation = uuid4()
        call = {
            "operation_id": str(operation),
            "policy_sha256": policy.policy_sha256,
            "tool": "records_read",
            "arguments": {"resource_id": str(own.id)},
        }
        result = client.post(route + "/calls", headers=headers, json=call)
        assert result.status_code == 200 and result.json()["status"] == "returned", result.text
        assert own.fields["private_note"] not in result.text
        repeated = client.post(route + "/calls", headers=headers, json=call)
        assert repeated.json()["replayed"] and LocalTestRuntime.calls == 1
        status = client.get(route + "/calls/" + str(operation), headers=headers)
        assert status.json()["result_available"] and "Owned support case" not in status.text
        assert (
            client.post(
                route + "/calls",
                headers=headers,
                json={**call, "arguments": {"resource_id": str(uuid4())}},
            ).status_code
            == 409
        )
        assert client.post(route + "/snapshot", headers=headers, json={}).status_code == 422
        snapshot = client.post(
            route + "/snapshot", headers=headers, json={"purpose": "owned-final-state-check"}
        )
        assert snapshot.status_code == 200 and str(own.id) in snapshot.json()["state"]
        assert client.delete(route, headers=headers).json()["cleanup_confirmed"]
        assert "grants" not in app.state.sessions[policy.session_id].policy.model_dump()
        assert not client.get(route + "/calls/" + str(operation), headers=headers).json()[
            "result_available"
        ]


def test_lost_tool_response_remains_uncertain_and_revoked_client_can_only_contain(monkeypatch):
    app, headers, _, policy, own, body = setup(monkeypatch)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with TestClient(app, base_url="https://owned-broker.invalid") as client:
        assert client.post(path, headers=headers, json=body).json()["state"] == "READY"
        route = path + "/" + str(policy.session_id)
        monkeypatch.setattr(LocalTestRuntime, "lose_ack", True)
        call = {
            "operation_id": str(uuid4()),
            "policy_sha256": policy.policy_sha256,
            "tool": "records_patch",
            "arguments": {
                "resource_id": str(own.id),
                "expected_version": 0,
                "updates": {"status": "resolved"},
            },
        }
        response = client.post(route + "/calls", headers=headers, json=call)
        assert response.json()["state"] == "UNCERTAIN" and response.json()["result"] is None
        assert (
            client.post(route + "/calls", headers=headers, json=call).json()["state"] == "UNCERTAIN"
        )
        assert LocalTestRuntime.calls == 1
        app.state.clients[0].enabled = False
        assert (
            client.post(
                route + "/snapshot", headers=headers, json={"purpose": "revoked"}
            ).status_code
            == 401
        )
        assert client.post(path, headers=headers, json=body).status_code == 401
        assert client.get(route, headers=headers).status_code == 200
        assert client.delete(route, headers=headers).json()["cleanup_confirmed"]


def test_schema_errors_and_large_or_unencrypted_requests_never_echo_content(monkeypatch):
    app, headers, _, policy, _, body = setup(monkeypatch)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with TestClient(app, base_url="https://owned-broker.invalid") as client:
        invalid = client.post(
            path, headers=headers, json={**body, "unexpected": "SYNTHETIC_PRIVATE_ARGUMENT"}
        )
        assert invalid.status_code == 422 and "SYNTHETIC_PRIVATE_ARGUMENT" not in invalid.text
        assert (
            client.post(
                path,
                headers={**headers, "Content-Type": "application/json"},
                content=b"x" * 1048577,
            ).status_code
            == 413
        )
        assert (
            client.post(
                path, headers={**headers, "Content-Encoding": "gzip"}, json=body
            ).status_code
            == 415
        )
    with TestClient(app, base_url="http://owned-broker.invalid") as client:
        assert client.get("/v1/identity", headers=headers).status_code == 400


def test_removed_environment_scope_cannot_replay_content_but_can_contain(monkeypatch):
    app, headers, _, policy, own, body = setup(monkeypatch)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with TestClient(app, base_url="https://owned-broker.invalid") as client:
        assert client.post(path, headers=headers, json=body).json()["state"] == "READY"
        route = path + "/" + str(policy.session_id)
        call = {
            "operation_id": str(uuid4()),
            "policy_sha256": policy.policy_sha256,
            "tool": "records_read",
            "arguments": {"resource_id": str(own.id)},
        }
        assert (
            client.post(route + "/calls", headers=headers, json=call).json()["status"] == "returned"
        )
        app.state.clients[0].environments = ("other-environment",)
        assert client.post(route + "/calls", headers=headers, json=call).status_code == 403
        assert (
            client.post(
                route + "/snapshot", headers=headers, json={"purpose": "scope-change"}
            ).status_code
            == 409
        )
        assert client.delete(route, headers=headers).json()["cleanup_confirmed"]


@pytest.mark.parametrize("snapshot", [False, True])
def test_cancel_during_io_never_returns_content_or_reopens_session(monkeypatch, snapshot):
    app, headers, _, policy, own, body = setup(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    method = "owner_snapshot" if snapshot else "execute"
    original = getattr(LocalTestRuntime, method)

    def delayed(runtime, *args):
        result = original(runtime, *args)
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(LocalTestRuntime, method, delayed)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with (
        TestClient(app, base_url="https://owned-broker.invalid") as client,
        ThreadPoolExecutor(max_workers=1) as requests,
    ):
        assert client.post(path, headers=headers, json=body).json()["state"] == "READY"
        route = path + "/" + str(policy.session_id)
        request_body = (
            {"purpose": "owned-evaluation"}
            if snapshot
            else {
                "operation_id": str(uuid4()),
                "policy_sha256": policy.policy_sha256,
                "tool": "records_read",
                "arguments": {"resource_id": str(own.id)},
            }
        )
        pending = requests.submit(
            client.post,
            route + ("/snapshot" if snapshot else "/calls"),
            headers=headers,
            json=request_body,
        )
        try:
            assert entered.wait(5)
            assert client.delete(route, headers=headers).json()["cleanup_confirmed"]
        finally:
            release.set()
        result = pending.result(timeout=5)
        assert (
            "Owned support case" not in result.text
            and own.fields["private_note"] not in result.text
        )
        assert (
            result.status_code == 409
            if snapshot
            else result.json()["state"] == "FINISHED_AFTER_CANCEL"
        )
        assert client.get(route, headers=headers).json()["state"] == "CLOSED"


def test_concurrent_start_reserves_capacity_before_constructor_and_cancel_prevents_readiness(
    monkeypatch,
):
    app, headers, _, policy, _, body = setup(monkeypatch, maximum=1)
    entered, release = threading.Event(), threading.Event()
    original = LocalTestRuntime.__init__

    def delayed(runtime, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        original(runtime, *args, **kwargs)

    monkeypatch.setattr(LocalTestRuntime, "__init__", delayed)
    path = "/v1/projects/" + str(policy.project_id) + "/sessions"
    with (
        TestClient(app, base_url="https://owned-broker.invalid") as client,
        ThreadPoolExecutor(max_workers=1) as requests,
    ):
        pending = requests.submit(client.post, path, headers=headers, json=body)
        try:
            assert entered.wait(5)
            assert client.post(path, headers=headers, json=body).json()["state"] == "STARTING"
            second = {**body, "policy": {**body["policy"], "session_id": str(uuid4())}}
            assert client.post(path, headers=headers, json=second).status_code == 429
            route = path + "/" + str(policy.session_id)
            assert client.delete(route, headers=headers).json()["state"] == "UNCERTAIN"
        finally:
            release.set()
        assert pending.result(timeout=5).json()["state"] == "CLOSED"
        assert LocalTestRuntime.starts == 1 and LocalTestRuntime.calls == 0
