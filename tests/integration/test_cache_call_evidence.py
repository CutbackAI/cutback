"""Durable cache delivery evidence, old unknowns, tenant scope and guarded schema rollback."""

from importlib.resources import files
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from support_exact_cache import prepare_cache_experiment

from cutback.server.execution_db import ExecutionCall
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_cache_deliveries_have_durable_separate_identity_cost_basis_and_scoped_reads(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    store, run_id, executor, calls = prepare_cache_experiment(api, tenant, tmp_path, monkeypatch)
    try:
        result = run_one(
            api[2].state.sessions,
            store,
            executor=executor,
            execution_enabled=True,
            only_run_id=run_id,
        )
        assert result["status"] == "COMPLETED" and len(calls) == 9
        response = api[0].get(
            f"/v1/projects/{tenant[0]}/experiments/{run_id}/calls", headers=tenant[2]
        )
        assert response.status_code == 200
        rows = response.json()["items"]
        cache_rows = [row for row in rows if row["cache_observation"] is not None]
        assert len(cache_rows) == 6
        hits = [
            row for row in cache_rows if row["cache_observation"]["origin"] == "encrypted_cache"
        ]
        assert len(hits) == 3 and all(row["response_model"] is None for row in hits)
        assert all(
            row["cache_observation"]["historical_response_model"] == "gpt-4.1" for row in hits
        )
        assert all(row["cache_observation"]["provider_modeled_cost_usd"] == "0" for row in hits)
        assert all(
            row["cache_observation"]["cache_operation_cost_basis_ref"]
            == "owned-synthetic-compute-bound"
            for row in cache_rows
        )
        assert all(
            row["cache_observation"]["invoice_verified_cost_usd"] is None for row in cache_rows
        )
        assert '"input":' not in response.text and '"output":' not in response.text
        other = api[0].post(
            "/v1/projects", headers=api[1], json={"slug": "owned-cache-evidence-foreign"}
        )
        assert other.status_code == 201
        denied = api[0].get(
            f"/v1/projects/{other.json()['id']}/experiments/{run_id}/calls", headers=tenant[2]
        )
        assert denied.status_code == 403
        denied = api[0].get(
            f"/v1/projects/{other.json()['id']}/experiments/{run_id}/calls", headers=api[1]
        )
        assert denied.status_code == 404
        with api[2].state.sessions() as session:
            stored = session.scalars(
                select(ExecutionCall).where(ExecutionCall.run_id == run_id)
            ).all()
            assert sum(row.cache_observation is not None for row in stored) == 6
            assert all(
                row.cache_observation is None for row in stored if row.model_key == "baseline"
            )
        cfg = Config()
        cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
        monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
        with pytest.raises(RuntimeError, match="DELETE_CACHE_CALL_EVIDENCE_BEFORE_DOWNGRADE"):
            command.downgrade(cfg, "0030")
        command.check(cfg)
        with api[2].state.sessions() as session:
            assert session.get(ExecutionCall, UUID(hits[0]["id"])).cache_observation is not None
    finally:
        executor.close()


def test_older_receipts_keep_unknown_cache_coverage_across_upgrade(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    from decimal import Decimal

    from test_execution_ledger import ledger_for

    from cutback.evals.experiments import Execution

    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(identifier, Execution("", Decimal("0.0002"), 2), "completed")
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    command.downgrade(cfg, "0030")
    command.upgrade(cfg, "head")
    command.check(cfg)
    with ledger.sessions() as session:
        row = session.get(ExecutionCall, identifier)
        assert row.cache_observation is None and row.response_model is None
        assert row.spent_usd == Decimal("0.0002")
