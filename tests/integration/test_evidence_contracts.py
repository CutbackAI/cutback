"""Real PostgreSQL guards for stage promotion and encrypted case retention."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import SyntheticExecutor, authorized_dataset, plan_for

from cutback.evals.datasets import digest
from cutback.server.case_evidence import recorder
from cutback.server.db import CapturePolicyRecord, ExperimentPlanRecord, PayloadSample
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


def test_stage_contract_quality_and_consumed_approval(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    base, store, dataset, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    specification = plan_for(
        {"immutable_hash": dataset["immutable_hash"]},
        staged=True,
        stage="smoke",
        split="validation",
        sample_limit=3,
    ).model_dump(mode="json")

    def plan(spec):
        return client.post(
            base + "/experiments/plans",
            headers=admin,
            json={"dataset_id": dataset["id"], "specification": spec},
        )

    def execute(spec, wrong=False):
        response = plan(spec)
        assert response.status_code == 201, response.text
        frozen = response.json()
        assert (
            client.post(
                base + f"/experiments/plans/{frozen['id']}/approve",
                headers=admin,
                json={"plan_hash": frozen["plan_hash"], "approval_ref": "synthetic-stage"},
            ).status_code
            == 200
        )
        queued = client.post(base + "/experiments", headers=admin, json={"plan_id": frozen["id"]})
        assert queued.status_code == 202, queued.text
        assert (
            client.post(
                base + "/experiments", headers=admin, json={"plan_id": frozen["id"]}
            ).status_code
            == 403
        )
        run_one(
            app.state.sessions,
            store,
            executor=SyntheticExecutor(wrong=wrong),
            execution_enabled=True,
        )
        return queued.json()["id"]

    smoke = execute(specification)
    pilot = {**specification, "stage": "pilot", "sample_limit": 5, "predecessor_run_id": smoke}
    assert plan(pilot).status_code == 201
    changed = {**pilot, "metric": {**pilot["metric"], "minimum": 0.99}}
    response = plan(changed)
    assert response.status_code == 409 and response.json()["code"] == "STAGE_CONTRACT_CHANGED"
    failed_smoke = execute(specification, wrong=True)
    response = plan({**pilot, "predecessor_run_id": failed_smoke})
    assert (
        response.status_code == 409
        and response.json()["code"] == "PREVIOUS_STAGE_QUALITY_GATE_FAILED"
    )


def test_case_retention_quota_expiry_and_old_plan_reapproval(api, tenant, tmp_path, monkeypatch):
    client, admin, app = api
    base, store, dataset, frozen = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions.begin() as session:
        policy = session.scalar(
            select(CapturePolicyRecord).where(CapturePolicyRecord.project_id == tenant[0])
        )
        policy_id = policy.id
        used = len(
            session.scalars(select(PayloadSample).where(PayloadSample.policy_id == policy_id)).all()
        )
        policy.policy = {**policy.policy, "max_samples": used + 1}
    record = recorder(app.state.sessions, store, UUID(tenant[0]), uuid4(), str(policy_id))
    value = {
        "case": {"id": "synthetic", "input": "owned synthetic text"},
        "model_key": "baseline",
        "output": "A",
    }
    record(value)
    with pytest.raises(ValueError, match="QUOTA_EXCEEDED"):
        record(value)
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, policy_id)
        policy.policy = {
            **policy.policy,
            "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        }
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        record(value)
    # An old frozen schema must not inherit new defaults under an existing approval.
    with app.state.sessions.begin() as session:
        row = session.get(ExperimentPlanRecord, UUID(frozen["id"]))
        raw = json.loads(store.get(UUID(tenant[0]), row.specification["artifact_ref"]))
        raw.pop("retain_case_outputs")
        ref = store.put(UUID(tenant[0]), json.dumps(raw).encode(), 1)
        row.specification = {**row.specification, "artifact_ref": ref, "plan_hash": digest(raw)}
        policy = session.get(CapturePolicyRecord, policy_id)
        policy.policy = {
            **policy.policy,
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    response = client.post(
        base + f"/experiments/plans/{frozen['id']}/content",
        headers=admin,
        json={"purpose": "synthetic-compatibility"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PLAN_SCHEMA_CHANGED_REAPPROVAL_REQUIRED"
    with app.state.sessions.begin() as session:
        row = session.get(ExperimentPlanRecord, UUID(frozen["id"]))
        raw["metric"]["kind"] = "synthetic-sensitive-invalid-contract"
        ref = store.put(UUID(tenant[0]), json.dumps(raw).encode(), 1)
        row.specification = {**row.specification, "artifact_ref": ref, "plan_hash": digest(raw)}
    response = client.post(
        base + f"/experiments/plans/{frozen['id']}/content",
        headers=admin,
        json={"purpose": "synthetic-compatibility"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "PLAN_SCHEMA_INVALID_REAPPROVAL_REQUIRED"
    assert "synthetic-sensitive" not in response.text
