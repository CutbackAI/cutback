from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from test_execution_ledger import ledger_for

from cutback.server.execution_db import ExecutionCall

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_native_ledger_downgrade_cannot_destroy_reserved_or_cumulative_billing_evidence(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    from decimal import Decimal

    ledger = ledger_for(api, tenant, tmp_path, monkeypatch)
    identifier = ledger.reserve(Decimal("0.001"), ledger.plan.baseline, "evaluation")
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="DELETE_NATIVE_LEDGER_DATA_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0012")
    command.check(cfg)
    with ledger.sessions() as session:
        assert (
            session.scalar(select(ExecutionCall.id).where(ExecutionCall.id == identifier))
            == identifier
        )
