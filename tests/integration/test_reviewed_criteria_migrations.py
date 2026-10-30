"""Real tenant constraints and refusal to erase criteria lineage on downgrade."""

from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from support_evaluation import authorized_dataset
from test_reviewed_criteria_workflow import prepared

from cutback.server.criteria_db import CriteriaExperimentUse, CriteriaVersionRecord
from cutback.server.db import DatasetRecord, ExperimentPlanRecord

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_criteria_populated_downgrade_preserves_version_and_tenant_foreign_keys(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    base, store, body = prepared(api, tenant, tmp_path, monkeypatch)
    drafted = api[0].post(base + "/criteria/drafts", headers=api[1], json=body)
    assert drafted.status_code == 201, drafted.text
    identifier = UUID(drafted.json()["id"])
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="CRITERIA_DOWNGRADE_REQUIRES_OFFLINE_RECOVERY_REVIEW"):
        command.downgrade(cfg, "0026")
    command.check(cfg)
    other = (
        api[0]
        .post(
            "/v1/projects", headers=api[1], json={"slug": "synthetic-criteria-other-" + uuid4().hex}
        )
        .json()["id"]
    )
    _, _, other_dataset, other_plan = authorized_dataset(
        api, (other, tenant[1], tenant[2]), tmp_path, monkeypatch, store=store
    )
    with api[2].state.sessions() as session:
        foreign_dataset = session.get(DatasetRecord, UUID(other_dataset["id"]))
        foreign_plan = session.get(ExperimentPlanRecord, UUID(other_plan["id"]))
        foreign_scope = {
            "dataset_id": foreign_dataset.id,
            "capture_policy_id": UUID(foreign_dataset.provenance["policy_id"]),
            "generation_plan_id": foreign_plan.id,
        }
    for field, wrong in foreign_scope.items():
        with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
            row = session.get(CriteriaVersionRecord, identifier)
            setattr(row, field, wrong)
            session.flush()
    with pytest.raises(IntegrityError), api[2].state.sessions.begin() as session:
        session.add(
            CriteriaExperimentUse(
                project_id=UUID(other),
                plan_id=UUID(other_plan["id"]),
                criteria_id=identifier,
                version_sha256="a" * 64,
                review_approval_id=uuid4(),
            )
        )
        session.flush()
    with api[2].state.sessions() as session:
        row = session.get(CriteriaVersionRecord, identifier)
        assert row.dataset_id == UUID(body["dataset_id"])
        assert row.review_status == "PENDING"
        assert (
            session.scalar(
                select(CriteriaExperimentUse.plan_id).where(
                    CriteriaExperimentUse.criteria_id == identifier
                )
            )
            is None
        )
