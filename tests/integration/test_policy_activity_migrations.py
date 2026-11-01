"""Real additive migration drift, existing-project preservation and scope enforcement."""

from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_policy_activity_scope import event, intake
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

from cutback.server.db import Environment, Project
from cutback.server.policy_activity_db import PolicyActivityRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_additive_roundtrip_preserves_existing_project_and_refuses_populated_downgrade(
    api, tenant, monkeypatch, api_database_url
):
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    command.check(cfg)
    command.downgrade(cfg, "0034")
    with api[2].state.engine.connect() as connection:
        assert "source_policy_activity" not in inspect(connection).get_table_names()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0034"
    command.upgrade(cfg, "head")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.get(Project, UUID(tenant[0])) is not None
    assert intake(api, tenant, event()).status_code == 200
    with pytest.raises(ValueError, match="POLICY_ACTIVITY_EXPORT_AND_REMOVE"):
        command.downgrade(cfg, "0034")
    with api[2].state.sessions.begin() as session:
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0035"
        session.execute(delete(PolicyActivityRecord))
    command.downgrade(cfg, "0034")
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_database_rejects_environment_from_another_project(api, tenant):
    project = (
        api[0]
        .post("/v1/projects", headers=api[1], json={"slug": "owned-other-" + uuid4().hex})
        .json()["id"]
    )
    with api[2].state.sessions() as session:
        environment = session.scalar(
            select(Environment.id).where(Environment.project_id == UUID(project))
        )
    record = event()
    with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
        session.add(
            PolicyActivityRecord(
                project_id=UUID(tenant[0]),
                environment_id=environment,
                event_id=uuid4(),
                workflow=record["workflow"],
                service_name=record["service_name"],
                source_commit=record["source_commit"],
                call_site_id=record["call_site_id"],
                policy_sha256=record["policy_sha256"],
                started_ns=record["started_ns"],
                fingerprint="d" * 64,
                metadata_record=record,
            )
        )
        session.flush()
