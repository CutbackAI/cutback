"""Real PostgreSQL one-shot tool quotas, compound scopes and destructive migration guard."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from integration.test_isolated_episodes import approved_record_run
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cutback.core.tool_policy import IsolatedToolPolicy
from cutback.evals.datasets import digest
from cutback.server.db import (
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
    Project,
)
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.plan_artifacts import load_plan
from cutback.server.tool_db import ToolOperationRecord, ToolSessionRecord
from cutback.server.tool_ledger import ToolJournal

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def prepared_journal(api, tenant, tmp_path, monkeypatch):
    base, store, run_id, project, principal, broker, image = approved_record_run(
        api, tenant, tmp_path, monkeypatch
    )
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, run_id)
        plan_row = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_plan(store, project, plan_row.specification)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        environment = session.scalar(
            select(Environment).where(
                Environment.project_id == project, Environment.name == "staging"
            )
        )
        run.status = job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=120)
        job_id, environment_id = job.id, environment.id
    # Primitive quota tests only. The native HTTP worker tests exercise current
    # approver, content and run authority; this stub does not claim those gates.
    ledger = ExecutionLedger(
        sessions, run_id, job_id, plan, environment_id, authorize=lambda session: None
    )
    ledger.start_episode(plan.baseline, "owned-primitive-case", 0, 4)
    journal = ToolJournal(ledger)
    policy = IsolatedToolPolicy(
        project_id=project,
        principal_id=principal,
        environment="staging",
        workflow="classify",
        session_id=uuid4(),
        image_id=image,
        max_calls=1,
        max_wall_seconds=30,
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        content_use_approved=True,
        grants=({"resource_id": str(uuid4()), "read_fields": ["status"]},),
    )
    journal.start(policy, uuid4())
    journal.started(
        policy.session_id,
        {
            "session_id": str(policy.session_id),
            "policy_sha256": policy.policy_sha256,
            "approval_hash": digest(plan.model_dump(mode="json")),
            "state": "READY",
        },
    )
    return base, ledger, journal, policy


def test_last_tool_slot_is_atomic_and_an_uncertain_identity_cannot_be_reused(
    api, tenant, tmp_path, monkeypatch
):
    _, ledger, journal, policy = prepared_journal(api, tenant, tmp_path, monkeypatch)

    def reserve(_):
        identifier = uuid4()
        try:
            journal.reserve(policy.session_id, identifier, "records_patch")
            return identifier
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(reserve, range(2)))
    assert sum(value is not None for value in identifiers) == 1
    identifier = next(value for value in identifiers if value is not None)
    journal.uncertain(policy.session_id, identifier)
    with pytest.raises(ValueError):
        journal.reserve(policy.session_id, identifier, "records_patch")
    with pytest.raises(ValueError):
        journal.start(policy, uuid4())
    with ledger.sessions() as session:
        row = session.get(ToolSessionRecord, policy.session_id)
        assert row.calls_used == 1 and row.state == "UNCERTAIN"
        operation = session.get(ToolOperationRecord, identifier)
        assert operation.state == "UNCERTAIN" and operation.state_changed is None
        assert not session.get(ExperimentRunRecord, ledger.run_id).requests_used


def test_compound_tool_scope_and_populated_downgrade_require_explicit_containment_and_delete(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    base, ledger, journal, policy = prepared_journal(api, tenant, tmp_path, monkeypatch)
    assert api[0].get("/v1/health/ready", headers=api[1]).json()["schema_revision"] == "0024"
    with pytest.raises(IntegrityError):
        with ledger.sessions.begin() as session:
            session.add(
                ToolOperationRecord(
                    id=uuid4(),
                    session_id=policy.session_id,
                    project_id=uuid4(),
                    environment_id=ledger.environment_id,
                    run_id=ledger.run_id,
                    sequence=1,
                    tool_kind="records_read",
                    state="DISPATCHING",
                    state_changed=None,
                )
            )
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    command.check(cfg)
    with pytest.raises(RuntimeError, match="ISOLATED_TOOL_DOWNGRADE_REQUIRES"):
        command.downgrade(cfg, "0023")
    assert (
        api[0]
        .post(base + "/experiments/" + str(ledger.run_id) + "/cancel", headers=api[1], json={})
        .status_code
        == 200
    )
    with ledger.sessions() as session:
        slug = session.get(Project, UUID(tenant[0])).slug
    assert api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code == 409
    journal.closed(
        policy.session_id,
        {
            "session_id": str(policy.session_id),
            "policy_sha256": policy.policy_sha256,
            "cleanup_confirmed": True,
            "state": "CLOSED",
        },
    )
    assert api[0].delete(base, headers=api[1], params={"confirm_slug": slug}).status_code == 200
    command.downgrade(cfg, "0023")
    command.upgrade(cfg, "head")
    command.check(cfg)
