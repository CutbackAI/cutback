"""Real additive migration refuses to discard asynchronous execution evidence."""

from importlib.resources import files
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from integration.test_provider_batch_native import approved

from cutback.server.db import Project

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_batch_downgrade_refuses_receipts_until_explicit_project_deletion(
    api, tenant, tmp_path, monkeypatch
):
    path, row, _ = approved(api, tenant, tmp_path, monkeypatch)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    with pytest.raises(RuntimeError, match="EXPLICITLY_RECONCILE_AND_DELETE_BATCH_DATA"):
        command.downgrade(cfg, "0020")
    assert (
        api[0]
        .post(path + "/provider-batches/" + row["id"] + "/cancel", headers=api[1], json={})
        .status_code
        == 200
    )
    with api[2].state.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    assert api[0].delete(path + "?confirm_slug=" + slug, headers=api[1]).status_code == 200
    command.downgrade(cfg, "0020")
    command.upgrade(cfg, "head")
    command.check(cfg)
