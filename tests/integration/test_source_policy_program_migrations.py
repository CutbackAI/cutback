"""Real PostgreSQL enforces source-program ownership and preserves existing data."""

from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_source_policy_program_api import prepared_program
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from cutback.server.db import Project
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.policy_program_db import SourcePolicyProgramPayload, SourcePolicyProgramRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def configuration(monkeypatch, url):
    monkeypatch.setenv("CUTBACK_DATABASE_URL", url)
    result = Config()
    result.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    return result


def test_empty_additive_schema_roundtrip_preserves_existing_project(
    api, tenant, api_database_url, monkeypatch
):
    cfg = configuration(monkeypatch, api_database_url)
    names = {
        "source_policy_programs",
        "source_policy_program_runs",
        "source_policy_program_selections",
        "source_policy_program_payloads",
    }
    command.check(cfg)
    command.downgrade(cfg, "0033")
    with api[2].state.engine.connect() as connection:
        assert not names.intersection(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0033"
    command.upgrade(cfg, "head")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.get(Project, UUID(tenant[0])) is not None
        assert names.issubset(inspect(session.connection()).get_table_names())
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_REVISION


def test_populated_schema_refuses_downgrade_and_invalid_scope_counters_or_payload_ownership(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    _, _, _, _, executor, calls, _, row, _ = prepared_program(api, tenant, tmp_path, monkeypatch)
    try:
        cfg = configuration(monkeypatch, api_database_url)
        with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_SOURCE_POLICY_PROGRAM_DATA"):
            command.downgrade(cfg, "0033")
        command.check(cfg)
        sessions, identifier = api[2].state.sessions, UUID(row["id"])
        foreign = api[0].post(
            "/v1/projects", headers=api[1], json={"slug": "owned-policy-other-" + uuid4().hex}
        )
        assert foreign.status_code == 201
        for field, value in {
            "project_id": UUID(foreign.json()["id"]),
            "reserved_usd": -1,
            "spent_usd": -1,
            "known_cost_requests": 1,
            "current_round": 4,
            "requests_used": 10001,
            "status": "INVENTED",
        }.items():
            with pytest.raises(IntegrityError), sessions.begin() as session:
                setattr(session.get(SourcePolicyProgramRecord, identifier), field, value)
                session.flush()
        with sessions() as session:
            link_id = session.scalar(
                select(SourcePolicyProgramPayload.id).where(
                    SourcePolicyProgramPayload.program_id == identifier
                )
            )
            assert link_id is not None
        for field, value in {
            "project_id": UUID(foreign.json()["id"]),
            "ordinal": 65,
            "kind": "INVENTED",
        }.items():
            with pytest.raises(IntegrityError), sessions.begin() as session:
                setattr(session.get(SourcePolicyProgramPayload, link_id), field, value)
                session.flush()
        assert calls == []
    finally:
        executor.close()
