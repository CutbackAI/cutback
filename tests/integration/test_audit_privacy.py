"""All retention paths honor the same global deny and current rights policy."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from support_evaluation import authorized_dataset

from cutback.server.case_evidence import recorder
from cutback.server.db import CapturePolicyRecord, PayloadSample

pytestmark = pytest.mark.postgres


def test_case_outputs_honor_forbidden_fields_and_redaction(api, tenant, tmp_path, monkeypatch):
    _, _, app = api
    _, store, _, _ = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    with app.state.sessions.begin() as session:
        row = session.scalar(
            select(CapturePolicyRecord).where(CapturePolicyRecord.project_id == UUID(tenant[0]))
        )
        policy_id = str(row.id)
        row.policy = {**row.policy, "never_capture_fields": ["output"]}
    run_id = uuid4()
    record = recorder(app.state.sessions, store, UUID(tenant[0]), run_id, policy_id)
    record(
        {
            "case": {"id": "synthetic-case", "input": "audit@example.invalid"},
            "model_key": "baseline",
            "output": "forbidden synthetic output",
        }
    )
    with app.state.sessions() as session:
        row = session.scalar(
            select(PayloadSample).where(
                PayloadSample.metadata_safe["run_id"].as_string() == str(run_id)
            )
        )
        value = json.loads(store.get(UUID(tenant[0]), row.object_ref))
    assert "output" not in value
    assert "audit@example.invalid" not in json.dumps(value)


def test_global_disable_prevents_worker_case_storage(api, tenant, tmp_path, monkeypatch):
    from fastapi import HTTPException

    _, _, app = api
    _, store, _, frozen = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    policy_id = frozen.get("policy_id")
    if not policy_id:
        with app.state.sessions() as session:
            policy_id = str(
                session.scalar(
                    select(CapturePolicyRecord).where(
                        CapturePolicyRecord.project_id == UUID(tenant[0])
                    )
                ).id
            )
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "false")
    with pytest.raises((ValueError, HTTPException), match="PAYLOAD_STORAGE_DISABLED"):
        recorder(app.state.sessions, store, UUID(tenant[0]), uuid4(), policy_id)(
            {
                "case": {"id": "synthetic-case", "input": "owned fixture"},
                "model_key": "baseline",
                "output": "A",
            }
        )
