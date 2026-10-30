"""Explicit provider storage/one-use approval on real PostgreSQL; no native network."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from support_evaluation import dataset_body, plan_for

from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentPlanRecord, JobRecord
from cutback.server.provider_batch_db import ProviderBatchRecord

pytestmark = pytest.mark.postgres


def prepare(api, tenant, tmp_path, monkeypatch, *, store=None):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    api[2].state.artifact_store = store or EncryptedFileStore(
        tmp_path / "encrypted", os.urandom(32)
    )
    path = f"/v1/projects/{tenant[0]}"
    policy = api[0].post(
        path + "/capture-policies",
        headers=api[1],
        json={
            "enabled": True,
            "approval_ref": "owned-batch-fixture",
            "rights_basis": "synthetic",
            "environments": ["staging"],
            "workflows": ["classify"],
            "allowed_providers": ["openai"],
            "retention_days": 7,
            "sample_rate": 1,
            "max_samples": 200,
            "expires_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
        },
    )
    assert policy.status_code == 201, policy.text
    value = dataset_body(80).model_copy(update={"policy_id": policy.json()["id"]})
    dataset = api[0].post(path + "/datasets", headers=api[1], json=value.model_dump(mode="json"))
    assert dataset.status_code == 201, dataset.text
    spec = plan_for(
        dataset.json(),
        split="validation",
        approved_providers=["openai"],
        baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1"},
        candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
        budget={"max_usd": "2", "max_requests": 200},
    )
    plan = api[0].post(
        path + "/experiments/plans",
        headers=api[1],
        json={"dataset_id": dataset.json()["id"], "specification": spec.model_dump(mode="json")},
    )
    assert plan.status_code == 201, plan.text
    return path, plan.json(), api[2].state.artifact_store


def test_batch_requires_storage_approval_then_freezes_one_use_native_budget(
    api, tenant, tmp_path, monkeypatch
):
    path, plan, _ = prepare(api, tenant, tmp_path, monkeypatch)
    body = {"plan_id": plan["id"]}
    assert api[0].post(path + "/provider-batches", headers=api[1], json=body).status_code == 422
    assert (
        api[0]
        .post(
            path + "/provider-batches",
            headers=api[1],
            json={**body, "provider_storage_approved": 1},
        )
        .status_code
        == 422
    )
    body["provider_storage_approved"] = True
    response = api[0].post(path + "/provider-batches", headers=api[1], json=body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["specification"]["automatic_resubmission"] is False
    assert row["specification"]["per_request_latency"] is None
    assert row["specification"]["zero_data_retention"] is False
    assert (
        api[0].post(path + "/provider-batches", headers=api[1], json=body).json()["id"] == row["id"]
    )
    assert api[0].post(path + "/provider-batches", headers=tenant[2], json=body).status_code == 403
    endpoint = path + "/provider-batches/" + row["id"]
    approved = api[0].post(
        endpoint + "/approve",
        headers=api[1],
        json={"approval_hash": row["approval_hash"], "approval_ref": "owned-approval"},
    )
    assert approved.status_code == 202, approved.text
    assert approved.json()["state"] == "QUEUED"
    assert (
        api[0]
        .post(
            endpoint + "/approve",
            headers=api[1],
            json={"approval_hash": row["approval_hash"], "approval_ref": "owned-approval"},
        )
        .status_code
        == 409
    )
    assert (
        api[0].post(path + "/experiments", headers=api[1], json={"plan_id": plan["id"]}).status_code
        == 403
    )
    with api[2].state.sessions() as session:
        frozen = session.get(ExperimentPlanRecord, UUID(plan["id"]))
        assert frozen.approval_status == "consumed"
        record = session.get(ProviderBatchRecord, UUID(row["id"]))
        job = session.get(JobRecord, record.job_id)
        assert job.execution_kind == "provider_batch"
        assert not record.calls and not record.pieces
    assert api[0].post(endpoint + "/cancel", headers=api[1], json={}).json()["state"] == "CANCELLED"
    assert api[0].get(endpoint, headers=tenant[2]).json()["state"] == "CANCELLED"


def test_batch_tenant_scope_is_checked_before_any_content_read(api, tenant, tmp_path, monkeypatch):
    path, plan, _ = prepare(api, tenant, tmp_path, monkeypatch)
    response = api[0].post(
        path + "/provider-batches",
        headers=api[1],
        json={"plan_id": plan["id"], "provider_storage_approved": True},
    )
    assert response.status_code == 201, response.text
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "other-synthetic-batch"})
        .json()["id"]
    )
    assert (
        api[0]
        .get(f"/v1/projects/{other}/provider-batches/{response.json()['id']}", headers=api[1])
        .status_code
        == 404
    )
    assert (
        api[0]
        .post(
            f"/v1/projects/{other}/provider-batches",
            headers=api[1],
            json={"plan_id": plan["id"], "provider_storage_approved": True},
        )
        .status_code
        == 409
    )
