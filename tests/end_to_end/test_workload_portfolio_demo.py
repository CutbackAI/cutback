"""Installed runnable sample: observed source through fixture-only trials and rollback."""

import os
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from support_http import owned_collector

from cutback.demo_workload_portfolio import seed_workload_portfolio
from cutback.evals.artifacts import EncryptedFileStore
from cutback.server.db import ExperimentPlanRecord, ExperimentRunRecord
from cutback.server.plan_artifacts import load_plan

pytestmark = pytest.mark.postgres


def test_owned_workload_sample_measures_confirms_and_checks_both_trees_without_live_grant(
    api, tenant, tmp_path, monkeypatch
):
    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    api[2].state.artifact_store = store
    token = api[1]["Authorization"][7:]
    with (
        owned_collector(api[2]) as endpoint,
        httpx.Client(base_url=endpoint, headers=api[1], timeout=30, trust_env=False) as control,
    ):
        result = seed_workload_portfolio(
            api[2], control, tenant[0], tmp_path, tenant[1]["Authorization"][7:]
        )
    assert result["observed_native_calls"] == 8 and result["modeled_potential_hits"] == 3
    assert result["paid_provider_calls"] == 0 and result["native_sdk_calls"] > 8
    assert result["rollback_verified"] and not result["runtime_cache_activated"]
    assert result["real_model_quality_verified"] is False
    assert result["source_change"]["synthetic_execution"]["customer_savings_verified"] is False
    assert result["application_checks"]["receipt"]["all_passed"]
    source = Path(result["source_directory"])
    assert not any(token.encode() in p.read_bytes() for p in source.rglob("*") if p.is_file())
    with api[2].state.sessions() as session:
        plans = session.query(ExperimentPlanRecord).filter_by(project_id=UUID(tenant[0])).all()
        assert len(plans) == 2
        for row in plans:
            frozen = load_plan(store, row.project_id, row.specification)
            assert frozen.native_fixture is not None and frozen.workload_portfolio is not None
        for identity in (result["validation_run_id"], result["confirmation_run_id"]):
            run = session.get(ExperimentRunRecord, UUID(identity))
            assert run.status == "COMPLETED" and run.results["billing_complete"]
            assert run.results["native_fixture"]["real_model_quality_verified"] is False
            assert run.results["synthetic_execution"]["customer_savings_verified"] is False
