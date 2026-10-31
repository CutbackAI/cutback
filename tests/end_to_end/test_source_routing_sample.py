"""Installed-compatible source routing demo uses actual HTTP, PG and checked Git trees."""

import json
from uuid import UUID

import httpx
import pytest
from test_pipeline import server

from cutback.demo_source_routing import seed_source_routing
from cutback.server.db import ExperimentRunRecord

pytestmark = [pytest.mark.postgres, pytest.mark.e2e]


def test_source_routing_http_agent_native_checks_local_review_and_rollback(
    api, tenant, tmp_path, monkeypatch
):
    import os

    from cutback.evals.artifacts import EncryptedFileStore

    monkeypatch.setenv("CUTBACK_ALLOW_PAYLOADS", "true")
    app = api[2]
    app.state.artifact_store = EncryptedFileStore(tmp_path / "encrypted", os.urandom(32))
    with (
        server(app) as endpoint,
        httpx.Client(
            base_url=endpoint,
            headers=api[1],
            trust_env=False,
            timeout=30,
        ) as control,
    ):
        result = seed_source_routing(app, control, tenant[0], tmp_path)
        assert result["source_bound"] and result["source_review_ready"]
        assert result["both_application_trees_checked"] and result["byte_exact_rollback_checked"]
        assert result["paid_provider_calls"] == 0 and result["full_task_cost_known"] is False
        assert result["production_changed"] is False
        with app.state.sessions() as session:
            runs = [
                session.get(ExperimentRunRecord, UUID(result[key + "_run_id"]))
                for key in ["training", "validation", "confirmation"]
            ]
            assert len({row.id for row in runs}) == 3
            assert all(
                row.status == "COMPLETED" and row.results["billing_complete"] for row in runs
            )
            assert result["native_sdk_calls"] == sum(row.requests_used for row in runs)
            assert runs[-1].results["lineage"]["fresh_test_claim"] is True
        checked = json.loads((tmp_path / "routing-source-checks.json").read_text("utf-8"))
        assert checked["all_passed"] is True
        exported = control.get(f"/v1/projects/{tenant[0]}/export")
        assert exported.status_code == 200
        assert b"Classify the declared ticket" not in exported.content
        assert b"Priority: urgent." not in exported.content
        assert b"object_ref" not in exported.content
