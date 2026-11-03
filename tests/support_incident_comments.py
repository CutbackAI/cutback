"""Independent synthetic incident and explicitly authorized encrypted comment store."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.monitoring_db import AlertRule, Incident, OnlineRule


def create_rule(api, tenant):
    now = datetime.now(UTC)
    result = api[0].post(
        f"/v1/projects/{tenant[0]}/online-rules",
        headers=api[1],
        json={
            "version_id": str(uuid4()),
            "key": "owned-comments-rule",
            "expected_revision": 0,
            "environment": "staging",
            "filter": {"workflow": "synthetic-support"},
            "evaluator": {"kind": "execution_ok"},
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "max_evaluations": 10,
            "approval_ref": "owned-synthetic-comment-rule",
        },
    )
    assert result.status_code == 201
    return result.json()


def prepare_collaboration(api, tenant, tmp_path, monkeypatch):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app, project = api[2], UUID(tenant[0])
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    rule = create_rule(api, tenant)
    now, alert_id, incident_id = datetime.now(UTC), uuid4(), uuid4()
    with app.state.sessions.begin() as session:
        session.get(OnlineRule, UUID(rule["id"])).enabled = False
        session.add(
            AlertRule(
                id=alert_id,
                project_id=project,
                online_rule_id=UUID(rule["id"]),
                key="owned-comment-alert",
                revision=1,
                specification={},
                enabled=False,
            )
        )
        session.flush()
        session.add(
            Incident(
                id=incident_id,
                project_id=project,
                alert_rule_id=alert_id,
                window_start=now - timedelta(minutes=10),
                window_end=now - timedelta(minutes=5),
                evidence={"coverage": "unknown"},
            )
        )
    policy = api[0].post(
        f"/v1/projects/{project}/capture-policies",
        headers=api[1],
        json={
            "enabled": True,
            "approval_ref": "owned-synthetic-collaboration",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["incident-comments"],
            "allowed_providers": ["local"],
            "expires_at": (now + timedelta(days=1)).isoformat(),
            "sample_rate": 1,
            "max_samples": 100,
            "max_payload_bytes": 64000,
        },
    )
    assert policy.status_code == 201
    return {
        "project": project,
        "incident": incident_id,
        "path": f"/v1/projects/{project}/incidents/{incident_id}/comments",
        "policy": policy.json()["id"],
        "directory": tmp_path / "encrypted",
    }


@pytest.fixture
def collaboration(api, tenant, tmp_path, monkeypatch):
    return prepare_collaboration(api, tenant, tmp_path, monkeypatch)
