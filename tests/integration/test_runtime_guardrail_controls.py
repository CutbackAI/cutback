"""Actual PostgreSQL approval, shared quota, revocation and encrypted retention."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from unit.test_guardrails import policy

from cutback.core.guardrails import GuardrailBinding
from cutback.evals.artifacts import EncryptedFileStore
from cutback.guardrails import GuardrailEngine
from cutback.server.db import AuditEvent, PayloadSample
from cutback.server.guardrail_controls import finish_check, reserve_check
from cutback.server.guardrail_db import GuardrailOperationRow, GuardrailPolicyRow

pytestmark = pytest.mark.postgres


def prepare(api, tenant, tmp_path, monkeypatch, *, max_checks=10, service_id=None):
    client, admin, app = api
    project = UUID(tenant[0])
    base = f"/v1/projects/{project}"
    definition = policy(project_id=str(project))
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    response = client.post(
        base + "/capture-policies",
        headers=admin,
        json={
            "enabled": True,
            "approval_ref": "owned-runtime-guardrails",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["owned-record-task"],
            "allowed_providers": ["local"],
            "sample_rate": 1,
            "max_samples": 10,
            "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    capture = response.json()["id"]
    body = {
        "service_id": str(service_id or uuid4()),
        "capture_policy_id": capture,
        "policy": definition.model_dump(mode="json"),
        "max_checks": max_checks,
    }
    path = base + "/runtime-guardrails"
    response = client.post(path, headers=admin, json=body)
    assert response.status_code == 201, response.text
    return definition, body, response.json(), path, store


def approve(api, state, path):
    approval = {
        "approval_hash": state["approval_hash"],
        "approval_ref": str(uuid4()),
        "expected_state_version": state["state_version"],
    }
    response = api[0].post(path + f"/{state['id']}/approve", json=approval, headers=api[1])
    assert response.status_code == 200, response.text
    return response.json(), approval


def test_exact_approval_encrypted_policy_audited_reads_rbac_and_public_metadata(
    api, tenant, tmp_path, monkeypatch
):
    definition, body, state, path, store = prepare(api, tenant, tmp_path, monkeypatch)
    client, admin, app = api
    assert state["state"] == "PENDING" and state["checks_used"] == 0
    assert client.post(path, json=body, headers=tenant[2]).status_code == 403
    assert client.post(path, json=body, headers=admin).json() == state
    changed = {**body, "max_checks": 11}
    assert client.post(path, json=changed, headers=admin).status_code == 409
    detail = client.get(path + f"/{state['id']}", headers=tenant[2])
    assert detail.status_code == 200
    for forbidden in ("SYNTHETIC_DENIED", "private-owner-rule", "content_nonce"):
        assert forbidden not in detail.text
    content_path = path + f"/{state['id']}/content"
    assert (
        client.post(content_path, json={"purpose": "owned-review"}, headers=tenant[2]).status_code
        == 403
    )
    content = client.post(content_path, json={"purpose": "owned-review"}, headers=admin)
    assert content.status_code == 200 and content.json() == definition.model_dump(mode="json")
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert b"SYNTHETIC_DENIED_LITERAL_OWNED" not in file.read_bytes()
    with app.state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PayloadSample)
                .where(PayloadSample.project_id == definition.project_id)
            )
            == 1
        )
        row = session.get(GuardrailPolicyRow, definition.policy_id)
        assert "terms" not in str(row.binding) and "content_nonce" not in str(row.binding)
        assert session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.project_id == definition.project_id,
                AuditEvent.action == "runtime_guardrail_content_read",
            )
        )
    bad = {"approval_hash": "a" * 64, "approval_ref": str(uuid4()), "expected_state_version": 0}
    assert client.post(path + f"/{state['id']}/approve", json=bad, headers=admin).status_code == 409
    state, activation = approve(api, state, path)
    assert state["state"] == "APPROVED" and state["state_version"] == 1
    assert (
        client.post(path + f"/{state['id']}/approve", json=activation, headers=admin).json()
        == state
    )
    activation["approval_ref"] = str(uuid4())
    assert (
        client.post(path + f"/{state['id']}/approve", json=activation, headers=admin).status_code
        == 409
    )
    export = client.get(f"/v1/projects/{definition.project_id}/export", headers=admin)
    assert export.status_code == 200 and "runtime_guardrail_policies" in export.text
    assert "SYNTHETIC_DENIED_LITERAL_OWNED" not in export.text
    assert "content_nonce" not in export.text


def test_durable_intent_before_check_no_replay_shared_final_slot_and_late_revocation(
    api, tenant, tmp_path, monkeypatch
):
    definition, _, state, path, _ = prepare(api, tenant, tmp_path, monkeypatch, max_checks=2)
    state, _ = approve(api, state, path)
    binding = GuardrailBinding.model_validate(state["binding"])
    project, sessions, operation = definition.project_id, api[2].state.sessions, uuid4()
    with sessions.begin() as session:
        reserve_check(session, project, definition.policy_id, binding, operation, "input")
    with sessions.begin() as session, pytest.raises(HTTPException, match="NO_AUTOMATIC_RETRY"):
        reserve_check(session, project, definition.policy_id, binding, operation, "input")

    def reserve():
        with sessions.begin() as session:
            try:
                reserve_check(session, project, definition.policy_id, binding, uuid4(), "input")
                return "reserved"
            except HTTPException as exc:
                return exc.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert results.count("reserved") == 1
    assert results.count("GUARDRAIL_SHARED_CHECK_QUOTA_EXHAUSTED") == 1
    decision = GuardrailEngine(definition).check(
        operation_id=operation,
        stage="input",
        content="benign",
        project_id=project,
        principal_id=definition.principal_id,
        environment="staging",
        workflow="owned-record-task",
    )
    response = api[0].post(
        path + f"/{state['id']}/disable",
        headers=api[1],
        json={
            "approval_hash": state["approval_hash"],
            "expected_state_version": 1,
        },
    )
    assert response.status_code == 200 and response.json()["state_version"] == 2
    with sessions.begin() as session:
        final = finish_check(session, project, definition.policy_id, binding, decision)
        assert final.action == "deny" and final.code == "GUARDRAIL_SCOPE_DENIED"
    read = api[0].get(path + f"/{state['id']}/operations/{operation}/input", headers=tenant[2])
    assert read.status_code == 200 and read.json()["decision"]["action"] == "deny"
    with sessions() as session:
        assert session.get(GuardrailPolicyRow, definition.policy_id).checks_used == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(GuardrailOperationRow)
                .where(GuardrailOperationRow.project_id == project)
            )
            == 2
        )


def test_capture_revocation_missing_policy_and_cross_tenant_fk_fail_closed(
    api, tenant, tmp_path, monkeypatch
):
    definition, body, state, path, _ = prepare(api, tenant, tmp_path, monkeypatch)
    state, _ = approve(api, state, path)
    binding = GuardrailBinding.model_validate(state["binding"])
    client, admin, app = api
    other = client.post(
        "/v1/projects", headers=admin, json={"slug": "other-" + uuid4().hex}
    ).json()["id"]
    assert (
        client.get(
            path.replace(str(definition.project_id), other) + f"/{state['id']}", headers=admin
        ).status_code
        == 404
    )
    assert (
        client.get(path.replace(str(definition.project_id), other), headers=tenant[2]).status_code
        == 403
    )
    with pytest.raises(IntegrityError), app.state.sessions.begin() as session:
        session.add(
            GuardrailPolicyRow(
                id=uuid4(),
                project_id=UUID(other),
                capture_policy_id=UUID(body["capture_policy_id"]),
                payload_id=uuid4(),
                binding=state["binding"],
                approval_hash=state["approval_hash"],
                max_checks=2,
                created_by="bootstrap_admin",
            )
        )
    assert (
        client.delete(
            f"/v1/projects/{definition.project_id}/capture-policies/{body['capture_policy_id']}",
            headers=admin,
        ).status_code
        == 200
    )
    with (
        app.state.sessions.begin() as session,
        pytest.raises(HTTPException, match="CONTENT_AUTHORITY"),
    ):
        reserve_check(
            session, definition.project_id, definition.policy_id, binding, uuid4(), "input"
        )
    ended = client.get(path + f"/{state['id']}", headers=tenant[2])
    assert ended.status_code == 200 and ended.json()["state"] == "APPROVED"
    assert ended.json()["execution_authority"] == "ended"
    assert (
        client.post(
            path + f"/{state['id']}/content", headers=admin, json={"purpose": "owned-review"}
        ).status_code
        == 403
    )
