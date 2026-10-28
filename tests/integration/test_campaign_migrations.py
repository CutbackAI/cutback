from importlib.resources import files
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from test_campaign_workflow import prepare

from cutback.server.campaign_db import CampaignRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_populated_campaign_downgrade_requires_explicit_data_deletion(
    api, tenant, api_database_url, tmp_path, monkeypatch
):
    _, _, row, _ = prepare(api, tenant, tmp_path, monkeypatch)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_CAMPAIGN_DATA_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0014")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.get(CampaignRecord, UUID(row["id"])) is not None
