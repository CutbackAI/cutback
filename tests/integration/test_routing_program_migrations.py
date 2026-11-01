"""Real PostgreSQL checks the additive schema and durable tenant boundaries."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from test_campaign_workflow import prepare

from cutback.server.campaign_db import CampaignEconomicReference, CampaignRecord
from cutback.server.db import ExperimentPlanRecord, Project
from cutback.server.migrate import CURRENT_REVISION
from cutback.server.routing_program_db import RoutingProgramRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def configuration(monkeypatch, url):
    monkeypatch.setenv("CUTBACK_DATABASE_URL", url)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    return cfg


def test_additive_program_schema_roundtrip_preserves_existing_project(
    api, tenant, api_database_url, monkeypatch
):
    cfg = configuration(monkeypatch, api_database_url)
    tables = {"routing_improvement_programs", "routing_program_runs", "routing_program_selections"}
    command.check(cfg)
    command.downgrade(cfg, "0032")
    with api[2].state.engine.connect() as connection:
        assert not tables.intersection(inspect(connection).get_table_names())
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0032"
    command.upgrade(cfg, "head")
    command.check(cfg)
    with api[2].state.sessions() as session:
        assert session.get(Project, UUID(tenant[0])) is not None
        assert tables.issubset(inspect(session.connection()).get_table_names())
        assert session.scalar(text("SELECT version_num FROM alembic_version")) == CURRENT_REVISION


def test_populated_program_refuses_downgrade_and_cross_tenant_dependencies(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    _, _, campaign, _ = prepare(api, tenant, tmp_path, monkeypatch)
    sessions = api[2].state.sessions
    identifier = uuid4()
    with sessions.begin() as session:
        source = session.get(CampaignRecord, UUID(campaign["id"]))
        reference = session.get(CampaignEconomicReference, source.id)
        template = session.scalar(
            select(ExperimentPlanRecord).where(
                ExperimentPlanRecord.project_id == source.project_id,
                ExperimentPlanRecord.dataset_id == source.dataset_id,
            )
        )
        session.add(
            RoutingProgramRecord(
                id=identifier,
                project_id=source.project_id,
                environment_id=source.environment_id,
                request_id=uuid4(),
                dataset_id=source.dataset_id,
                template_plan_id=template.id,
                snapshot_id=reference.snapshot_id,
                policy_id=source.policy_id,
                payload_id=source.payload_id,
                request_sha256="a" * 64,
                template_plan_sha256="b" * 64,
                snapshot_sha256=reference.snapshot_sha256,
                approval_sha256="c" * 64,
                specification={},
                economic_envelope={},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                actor="bootstrap_admin",
            )
        )
    cfg = configuration(monkeypatch, api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_ROUTING_PROGRAM_DATA"):
        command.downgrade(cfg, "0032")
    command.check(cfg)
    other = api[0].post(
        "/v1/projects", headers=api[1], json={"slug": "owned-routing-other-" + uuid4().hex}
    )
    assert other.status_code == 201
    other_ingest = api[0].post(
        f"/v1/projects/{other.json()['id']}/keys", headers=api[1], json={"role": "ingest"}
    )
    assert other_ingest.status_code == 201
    _, _, foreign, _ = prepare(
        api,
        (
            other.json()["id"],
            {"Authorization": f"Bearer {other_ingest.json()['token']}"},
            tenant[2],
        ),
        tmp_path / "other",
        monkeypatch,
    )
    with sessions() as session:
        foreign_row = session.get(CampaignRecord, UUID(foreign["id"]))
        foreign_ref = session.get(CampaignEconomicReference, foreign_row.id)
        foreign_plan = session.scalar(
            select(ExperimentPlanRecord.id).where(
                ExperimentPlanRecord.project_id == foreign_row.project_id
            )
        )
        wrong = {
            "environment_id": foreign_row.environment_id,
            "dataset_id": foreign_row.dataset_id,
            "policy_id": foreign_row.policy_id,
            "payload_id": foreign_row.payload_id,
            "fit_id": foreign_row.payload_id,
            "snapshot_id": foreign_ref.snapshot_id,
            "template_plan_id": foreign_plan,
            "reserved_usd": -1,
            "known_cost_requests": 1,
            "status": "INVENTED",
        }
    for field, value in wrong.items():
        with pytest.raises(IntegrityError), sessions.begin() as session:
            row = session.get(RoutingProgramRecord, identifier)
            setattr(row, field, value)
            session.flush()
    with sessions() as session:
        row = session.get(RoutingProgramRecord, identifier)
        assert row.project_id == UUID(tenant[0])
        assert row.status == "READY_FOR_APPROVAL" and row.requests_used == 0
