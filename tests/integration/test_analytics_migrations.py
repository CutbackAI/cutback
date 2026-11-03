"""Real additive scope constraint and guarded analytics rollback on owned PostgreSQL."""

from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_analytics_dashboards import create_chart
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from cutback.server.db import Environment, Project
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.monitoring_db import SavedView

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def configuration(monkeypatch, target):
    monkeypatch.setenv("CUTBACK_DATABASE_URL", target)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    return cfg


def test_real_roundtrip_preserves_projects_and_refuses_populated_analytics_rollback(
    api, tenant, api_database_url, monkeypatch
):
    cfg = configuration(monkeypatch, api_database_url)
    command.check(cfg)
    chart, _ = create_chart(api, tenant)
    with pytest.raises(ValueError, match="ANALYTICS_EXPORT_AND_REMOVE_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0036")
    with api[2].state.sessions() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_REVISION
        assert session.get(SavedView, UUID(chart["id"])) is not None
    result = api[0].delete(
        f"/v1/projects/{tenant[0]}/analytics-dashboards/{chart['id']}",
        headers=api[1],
        params={"expected_revision": 1},
    )
    assert result.status_code == 200
    command.downgrade(cfg, "0036")
    command.upgrade(cfg, "head")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.get(Project, UUID(tenant[0])) is not None


def test_database_rejects_cross_project_saved_view_environment(api, tenant):
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-other-" + uuid4().hex})
        .json()["id"]
    )
    with api[2].state.sessions() as session:
        env = session.scalar(select(Environment.id).where(Environment.project_id == UUID(other)))
    with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
        session.add(
            SavedView(
                id=uuid4(),
                project_id=UUID(tenant[0]),
                environment_id=env,
                key="owned-cross-scope",
                revision=1,
                specification={"page": "traces"},
            )
        )
        session.flush()


def test_legacy_invalid_environment_stops_upgrade_without_repairing_or_discarding_data(
    api, tenant, api_database_url, monkeypatch
):
    cfg = configuration(monkeypatch, api_database_url)
    other = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-old-" + uuid4().hex})
        .json()["id"]
    )
    command.downgrade(cfg, "0036")
    identifier = uuid4()
    with api[2].state.sessions.begin() as session:
        env = session.scalar(select(Environment.id).where(Environment.project_id == UUID(other)))
        session.add(
            SavedView(
                id=identifier,
                project_id=UUID(tenant[0]),
                environment_id=env,
                key="owned-legacy-cross-scope",
                revision=1,
                specification={"page": "traces"},
            )
        )
    with pytest.raises(IntegrityError):
        command.upgrade(cfg, "head")
    with api[2].state.sessions.begin() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0036"
        row = session.get(SavedView, identifier)
        assert row is not None and row.environment_id == env
        session.delete(row)
    command.upgrade(cfg, "head")
    command.check(cfg)
