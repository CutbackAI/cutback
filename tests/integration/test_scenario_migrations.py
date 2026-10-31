"""Real PostgreSQL preserves scenario lineage on downgrade and rejects cross-tenant links."""

from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support_evaluation import authorized_dataset

from cutback.server.db import DatasetRecord, Environment
from cutback.server.scenario_db import ScenarioDatasetImport, ScenarioVersionRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_scenario_rows_and_review_constraints_survive_refused_populated_downgrade(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    _, store, dataset, plan = authorized_dataset(api, tenant, tmp_path, monkeypatch)
    project = UUID(tenant[0])
    identifier = uuid4()
    with api[2].state.sessions.begin() as session:
        source = session.get(DatasetRecord, UUID(dataset["id"]))
        environment = session.scalar(
            select(Environment).where(
                Environment.project_id == project,
                Environment.name == source.provenance["environment"],
            )
        )
        session.add(
            ScenarioVersionRecord(
                id=identifier,
                project_id=project,
                dataset_id=source.id,
                environment_id=environment.id,
                capture_policy_id=UUID(source.provenance["policy_id"]),
                generation_plan_id=UUID(plan["id"]),
                input_payload_id=uuid4(),
                source_dataset_sha256=source.immutable_hash,
                generation_plan_sha256="a" * 64,
                created_by="bootstrap_admin",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="SCENARIO_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW"):
        command.downgrade(cfg, "0027")
    command.check(cfg)
    other = (
        api[0]
        .post(
            "/v1/projects",
            headers=api[1],
            json={
                "slug": "synthetic-scenario-other-" + uuid4().hex,
            },
        )
        .json()["id"]
    )
    _, _, foreign, foreign_plan = authorized_dataset(
        api,
        (other, tenant[1], tenant[2]),
        tmp_path,
        monkeypatch,
        store=store,
    )
    with api[2].state.sessions() as session:
        foreign_source = session.get(DatasetRecord, UUID(foreign["id"]))
        foreign_environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == UUID(other),
                Environment.name == foreign_source.provenance["environment"],
            )
        )
        wrong = {
            "dataset_id": foreign_source.id,
            "capture_policy_id": UUID(foreign_source.provenance["policy_id"]),
            "generation_plan_id": UUID(foreign_plan["id"]),
            "environment_id": foreign_environment,
            "state": "COMPLETED",
            "review_status": "APPROVED",
        }
    for field, value in wrong.items():
        with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
            row = session.get(ScenarioVersionRecord, identifier)
            setattr(row, field, value)
            session.flush()
    with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
        session.add(
            ScenarioDatasetImport(
                project_id=UUID(other),
                dataset_id=UUID(foreign["id"]),
                scenario_id=identifier,
                request_id=uuid4(),
                request_sha256="b" * 64,
                version_sha256="c" * 64,
                review_approval_id=uuid4(),
            )
        )
        session.flush()
    with api[2].state.sessions() as session:
        row = session.get(ScenarioVersionRecord, identifier)
        assert row.state == "PENDING" and row.review_status == "PENDING"
        assert row.dataset_id == UUID(dataset["id"])
