from importlib.resources import files

import pytest
from alembic import command
from alembic.config import Config
from support_outcome_watch import prepare_watch

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_pending_policy_downgrade_refuses_to_destroy_owner_intent(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    prepare_watch(api, tenant, tmp_path, monkeypatch, approve=False)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_OUTCOME_WATCH_DATA"):
        command.downgrade(cfg, "0017")
    command.check(cfg)
