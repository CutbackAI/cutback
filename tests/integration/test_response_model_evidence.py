"""Observed native identity survives accounting without guessing old metadata."""

from decimal import Decimal
from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from test_execution_ledger import ledger_for

from cutback.evals.experiments import Execution
from cutback.server.db import ExperimentRunRecord
from cutback.server.execution_db import ExecutionCall

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_observed_identity_is_distinct_idempotent_and_exported_without_payload(
    api, tenant, tmp_path, monkeypatch
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    observed = "gpt-4.1-2025-04-14"
    result = Execution(
        "synthetic-not-retained", Decimal("0.0002"), 2, 4, 3, response_model=observed
    )
    ledger.record(identifier, result, "completed")
    ledger.record(identifier, result, "completed")
    ledger.record(identifier, Execution("", Decimal("0.0002"), 2, 4, 3), "completed")
    with pytest.raises(ValueError, match="RESPONSE_MODEL_ALREADY_FINAL"):
        ledger.record(
            identifier,
            Execution("", Decimal("0.0002"), 2, 4, 3, response_model="different-model"),
            "completed",
        )
    with ledger.sessions() as session:
        row = session.get(ExecutionCall, identifier)
        assert row.model == ledger.plan.baseline.model and row.response_model == observed
        run = session.get(ExperimentRunRecord, ledger.run_id)
        assert run.spent_usd == Decimal("0.0002") and run.known_cost_requests == 1
        project = run.project_id
    response = api[0].get(
        f"/v1/projects/{project}/experiments/{ledger.run_id}/calls", headers=api[1]
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["response_model"] == observed
    assert "synthetic-not-retained" not in response.text
    viewer_response = api[0].get(
        f"/v1/projects/{project}/experiments/{ledger.run_id}/calls", headers=tenant[2]
    )
    assert viewer_response.status_code == 200
    assert viewer_response.json()["items"][0]["response_model"] == observed


@pytest.mark.parametrize("model", [None, "sk-synthetic-private-label"])
def test_missing_or_unsafe_model_never_uses_requested_model(
    api, tenant, tmp_path, monkeypatch, model
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(
        identifier, Execution("", Decimal("0.0002"), 2, response_model=model), "completed"
    )
    with ledger.sessions() as session:
        assert session.get(ExecutionCall, identifier).response_model is None


def test_late_response_preserves_observed_identity_and_blocks_lossy_downgrade(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(identifier, None, "uncertain")
    ledger.record(
        identifier,
        Execution("", Decimal("0.0002"), 2, response_model="gpt-4.1-2025-04-14"),
        "completed",
    )
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="DELETE_OBSERVED_MODEL_EVIDENCE_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0029")
    command.check(cfg)
    with ledger.sessions() as session:
        assert session.get(ExecutionCall, identifier).response_model == "gpt-4.1-2025-04-14"


def test_historical_receipt_remains_unknown_through_upgrade(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    ledger.record(identifier, Execution("", Decimal("0.0002"), 2), "completed")
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    command.downgrade(cfg, "0029")
    command.upgrade(cfg, "head")
    command.check(cfg)
    with ledger.sessions() as session:
        row = session.get(ExecutionCall, identifier)
        assert row.model == ledger.plan.baseline.model
        assert row.response_model is None and row.spent_usd == Decimal("0.0002")
