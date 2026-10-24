import hashlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from support_evaluation import SyntheticExecutor, dataset_body, plan_for

from cutback.evals.artifacts import EncryptedFileStore
from cutback.evals.datasets import digest
from cutback.scanner import scan
from cutback.server.db import DailyUsage, ExperimentRunRecord, StoredEvent
from cutback.server.operations import enforce_quota, maintain
from cutback.server.repository import PostgresEventRepository
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_inventory_rbac_health_and_opportunities(api, tenant, tmp_path):
    client, admin, _ = api
    project, ingest, viewer = tenant
    (tmp_path / "main.py").write_text(
        "from openai import OpenAI\nc=OpenAI()\n"
        'c.responses.create(model="gpt-4.1-mini", input="private")'
    )
    inventory = scan(tmp_path)
    path = f"/v1/projects/{project}"
    assert client.post(path + "/scans", headers=viewer, json=inventory).status_code == 403
    assert client.post(path + "/scans", headers=admin, json=inventory).status_code == 201
    saved = client.get(path + "/inventory", headers=viewer).json()["inventory"]
    assert saved["project"]["root"] == "."
    assert "private" not in str(saved)
    body = {
        "project_id": project,
        "instance_id": str(uuid4()),
        "environment": "staging",
        "service_name": "synthetic",
        "counters": {"observed": 5, "dropped": 1},
    }
    assert client.post("/v1/ingest/health", headers=viewer, json=body).status_code == 403
    assert client.post("/v1/ingest/health", headers=ingest, json=body).status_code == 200
    assert (
        client.get(path + "/health", headers=viewer).json()["instances"][0]["counters"]["dropped"]
        == 1
    )
    assert client.get(path + "/opportunities", headers=viewer).json()["opportunities"] == []


def test_effective_dated_contract_rates_and_shared_quota(api, tenant, event):
    client, admin, application = api
    project, ingest, viewer = tenant
    path = f"/v1/projects/{project}"
    override = {
        "provider": "openai",
        "model": event.response_model,
        "input": "0.1",
        "output": "0.2",
        "cache_read": "0.01",
        "valid_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
    }
    assert client.post(path + "/prices/overrides", headers=viewer, json=override).status_code == 403
    assert client.post(path + "/prices/overrides", headers=admin, json=override).status_code == 201
    client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [event.model_dump(mode="json")]},
    )
    record = client.get(path + "/events", headers=viewer).json()[0]
    assert record["cost_status"] == "contract_rate"
    assert Decimal(record["cost_usd"]) == Decimal("0.000102")
    key = hashlib.sha256(os.urandom(32)).hexdigest()
    enforce_quota(application.state.sessions, key, 1)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exceeded:
        enforce_quota(application.state.sessions, key, 1)
    assert exceeded.value.status_code == 429


def test_retention_atomic_idempotent_known_and_unknown_usage(api, tenant, event):
    client, _, app = api
    project, ingest, viewer = tenant
    client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [event.model_dump(mode="json")]},
    )
    old = (datetime.now(UTC) - timedelta(days=40)).replace(hour=12, minute=0)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
    assert maintain(app.state.sessions)["raw_events_eligible"] == 1
    maintain(app.state.sessions, apply=True)
    assert maintain(app.state.sessions, apply=True)["raw_events_eligible"] == 0
    with app.state.sessions() as session:
        assert not session.scalar(
            select(StoredEvent).where(StoredEvent.project_id == UUID(project))
        )
        assert session.scalar(select(DailyUsage).where(DailyUsage.project_id == UUID(project)))
        report = PostgresEventRepository().query_usage(
            session,
            UUID(project),
            old.replace(hour=0, minute=0, second=0, microsecond=0),
            datetime.now(UTC),
            "model",
        )
        assert report["summary"]["observed_attempts"] == 1
        assert Decimal(report["summary"]["modeled_cost_usd"]) == Decimal("0.0005")
        assert report["summary"]["input_tokens_observed"] == 1000


def test_project_retention_override_is_protected_from_shorter_global_default(api, tenant, event):
    from cutback.server.operations import maintain_all

    client, admin, app = api
    project, ingest, _ = tenant
    path = f"/v1/projects/{project}"
    client.post(
        "/v1/ingest/events",
        headers=ingest,
        json={"project_id": project, "events": [event.model_dump(mode="json")]},
    )
    old = datetime.now(UTC) - timedelta(days=40)
    with app.state.sessions.begin() as session:
        session.execute(
            update(StoredEvent)
            .where(StoredEvent.project_id == UUID(project))
            .values(occurred_at=old)
        )
    assert (
        client.put(
            path + "/settings", headers=admin, json={"raw_days": 45, "aggregate_days": 395}
        ).status_code
        == 200
    )
    maintain_all(app.state.sessions, raw_days=30, aggregate_days=395, apply=True)
    with app.state.sessions() as session:
        assert session.scalar(select(StoredEvent).where(StoredEvent.project_id == UUID(project)))
    client.put(path + "/settings", headers=admin, json={"raw_days": 10, "aggregate_days": 395})
    maintain_all(app.state.sessions, raw_days=30, aggregate_days=395, apply=True)
    with app.state.sessions() as session:
        assert not session.scalar(
            select(StoredEvent).where(StoredEvent.project_id == UUID(project))
        )


def test_encrypted_dataset_approved_budget_worker_and_review(api, tenant, monkeypatch, tmp_path):
    client, admin, app = api
    project, _, viewer = tenant
    path = f"/v1/projects/{project}"
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path, os.urandom(32))
    app.state.artifact_store = store
    policy = {
        "enabled": True,
        "approval_ref": "synthetic-test-only",
        "rights_basis": "synthetic",
        "environments": ["staging"],
        "workflows": ["classify"],
        "allowed_providers": ["local"],
        "max_samples": 100,
        "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    created = client.post(path + "/capture-policies", headers=admin, json=policy)
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    body = dataset_body().model_copy(update={"policy_id": policy_id})
    imported = client.post(path + "/datasets", headers=admin, json=body.model_dump(mode="json"))
    assert imported.status_code == 201, imported.text
    dataset = imported.json()
    metadata = client.get(path + "/datasets", headers=viewer).json()
    assert "Classify synthetic" not in str(metadata)
    spec = plan_for({"immutable_hash": dataset["immutable_hash"]})
    spec.baseline.instruction = "PRIVATE template variant"
    spec.candidates[0].instruction = "PRIVATE template variant"
    planned = client.post(
        path + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    )
    assert planned.status_code == 201, planned.text
    assert "PRIVATE" not in client.get(path + "/experiments/plans", headers=viewer).text
    plan = planned.json()
    assert (
        client.post(path + "/experiments", headers=admin, json={"plan_id": plan["id"]}).status_code
        == 403
    )
    approved = client.post(
        path + f"/experiments/plans/{plan['id']}/approve",
        headers=admin,
        json={
            "plan_hash": digest(spec.model_dump(mode="json")),
            "approval_ref": "synthetic-budget",
        },
    )
    assert approved.status_code == 200, approved.text
    queued = client.post(path + "/experiments", headers=admin, json={"plan_id": plan["id"]})
    assert queued.status_code == 202
    assert (
        client.post(path + "/experiments", headers=admin, json={"plan_id": plan["id"]}).status_code
        == 403
    )
    executor = SyntheticExecutor()
    assert run_one(app.state.sessions, store, executor=executor)["provider_calls"] == 0
    result = run_one(app.state.sessions, store, executor=executor, execution_enabled=True)
    assert result["status"] == "COMPLETED", result
    with app.state.sessions() as session:
        row = session.get(ExperimentRunRecord, UUID(queued.json()["id"]))
        assert row.requests_used == len(executor.calls)
        assert row.reserved_usd <= spec.budget.max_usd
        assert row.results["dataset_hash"] == dataset["immutable_hash"]
        assert "PRIVATE" not in str(row.results)
    rec = client.get(path + "/recommendations", headers=viewer).json()[0]
    assert rec["evidence"]["status"] == "PROMISING"
    reviewed = client.post(
        path + f"/recommendations/{rec['id']}/review",
        headers=admin,
        json={"decision": "accepted_for_staging", "reason": "synthetic-reviewed"},
    )
    assert reviewed.json()["deployment"] == "not_performed"
    # Kill the worker after its durable reservation but before a provider result is known.
    again = client.post(
        path + "/experiments/plans",
        headers=admin,
        json={"dataset_id": dataset["id"], "specification": spec.model_dump(mode="json")},
    ).json()
    assert (
        client.post(
            path + f"/experiments/plans/{again['id']}/approve",
            headers=admin,
            json={"plan_hash": again["plan_hash"], "approval_ref": "synthetic-crash-budget"},
        ).status_code
        == 200
    )
    crash_run = client.post(
        path + "/experiments", headers=admin, json={"plan_id": again["id"]}
    ).json()["id"]

    class Crashes(SyntheticExecutor):
        def execute(self, model, text, timeout_seconds):
            self.calls.append((model.key, text))
            raise SystemExit("synthetic worker crash")

    crashed = Crashes()
    with pytest.raises(SystemExit):
        run_one(app.state.sessions, store, executor=crashed, execution_enabled=True)
    from cutback.server.db import JobRecord

    with app.state.sessions.begin() as session:
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == UUID(crash_run)))
        job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    run_one(app.state.sessions, store, executor=crashed, execution_enabled=True)
    with app.state.sessions() as session:
        lost = session.get(ExperimentRunRecord, UUID(crash_run))
        assert lost.status == "FAILED" and lost.requests_used == 1 and lost.reserved_usd > 0
    assert len(crashed.calls) == 1  # A possibly billable request is never replayed automatically.
    assert client.delete(path + f"/capture-policies/{policy_id}", headers=admin).status_code == 200
    assert (
        client.post(
            path + "/datasets", headers=admin, json=body.model_dump(mode="json")
        ).status_code
        == 403
    )
