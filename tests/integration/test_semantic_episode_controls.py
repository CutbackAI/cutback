from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from test_native_episodes import prepared_episode

from cutback.evals.experiments import upper_cost
from cutback.server.db import (
    CapturePolicyRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
)
from cutback.server.execution_db import ExecutionCall
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.plan_artifacts import load_plan

pytestmark = pytest.mark.postgres


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_semantic_purpose_slots_are_separate_atomic_and_require_an_active_episode(
    api, tenant, tmp_path, monkeypatch
):
    _, store, run_id, _ = prepared_episode(
        api, tenant, tmp_path, monkeypatch, semantic=True, max_turns=32
    )
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, run_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        plan = load_plan(
            store, run.project_id, session.get(ExperimentPlanRecord, run.plan_id).specification
        )
        environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == run.project_id, Environment.name == "staging"
            )
        )
        run.status = job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=120)
        job_id = job.id
    ledger = ExecutionLedger(sessions, run_id, job_id, plan, environment, authorize=lambda s: None)
    with pytest.raises(ValueError, match="ACTIVE_TARGET_EPISODE"):
        ledger.reserve(upper_cost(plan.judge), plan.judge, "judge")
    ledger.start_episode(plan.baseline, "owned-slot-case", 0, 33)
    with pytest.raises(ValueError, match="PURPOSE_AND_MODEL"):
        ledger.reserve(upper_cost(plan.baseline), plan.baseline, "judge")
    for _ in range(32):
        ledger.reserve(upper_cost(plan.baseline), plan.baseline, "evaluation")
    with pytest.raises(ValueError, match="PURPOSE_REQUEST_BOUND"):
        ledger.reserve(upper_cost(plan.baseline), plan.baseline, "evaluation")

    def reserve_judge(_):
        try:
            return ledger.reserve(upper_cost(plan.judge), plan.judge, "judge")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve_judge, range(2)))
    assert sum(isinstance(value, UUID) for value in outcomes) == 1
    assert "EXECUTION_EPISODE_PURPOSE_REQUEST_BOUND_EXHAUSTED" in outcomes
    with sessions() as session:
        assert (
            len(session.scalars(select(ExecutionCall).where(ExecutionCall.run_id == run_id)).all())
            == 33
        )


def test_guided_draft_freezes_encrypted_semantics_and_refuses_unapproved_judge_vendor(
    api, tenant, tmp_path, monkeypatch
):
    base, _, run_id, policy_id = prepared_episode(api, tenant, tmp_path, monkeypatch, semantic=True)
    client, admin, app = api
    assert (
        client.post(base + f"/experiments/{run_id}/cancel", headers=admin, json={}).status_code
        == 200
    )
    with app.state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        row = session.get(ExperimentPlanRecord, run.plan_id)
        public = row.specification["public_specification"]
        assert (
            public["episode_execution"]["semantic_judging"]["criteria"][0]["judge_rubric"] is None
        )
        assert "judge_rubric_sha256" not in str(public)
        dataset_id = str(row.dataset_id)
    body = {
        "dataset_id": dataset_id,
        "baseline": {"key": "baseline", "provider": "anthropic", "model": "claude-haiku-4-5"},
        "candidates": [
            {
                "key": "variant",
                "provider": "anthropic",
                "model": "claude-haiku-4-5",
                "instruction": "Own reviewed instruction",
            }
        ],
        "episode_max_turns": 4,
        "episode_judge": {
            "key": "judge",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "max_input_tokens": 64000,
            "max_output_tokens": 2048,
        },
        "episode_judging": {
            "criteria": [
                {
                    "key": "answer",
                    "kind": "answer_correctness",
                    "judge_rubric": "PRIVATE_DRAFT_RUBRIC: compare the answer with the reference",
                }
            ]
        },
        "max_usd": "1",
    }
    response = client.post(base + "/experiments/draft", headers=admin, json=body)
    assert response.status_code == 201, response.text
    assert "PRIVATE_DRAFT_RUBRIC" not in response.text
    assert response.json()["evaluator_version"] == "deterministic-v5"
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(policy_id))
        policy.policy = {**policy.policy, "allowed_providers": ["anthropic"]}
    assert client.post(base + "/experiments/draft", headers=admin, json=body).status_code == 403


def test_populated_v5_downgrade_requires_explicit_export_and_delete(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    prepared_episode(api, tenant, tmp_path, monkeypatch, semantic=True)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="EXPLICITLY_DELETE_EPISODE_JUDGE_DATA"):
        command.downgrade(cfg, "0016")
    command.check(cfg)


def test_lost_plan_commit_acknowledgement_preserves_encrypted_judge_contract(
    api, tenant, tmp_path, monkeypatch
):
    from contextlib import contextmanager

    base, store, run_id, _ = prepared_episode(api, tenant, tmp_path, monkeypatch, semantic=True)
    client, admin, app = api
    sessions = app.state.sessions
    with sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        original = session.get(ExperimentPlanRecord, run.plan_id)
        specification = load_plan(store, run.project_id, original.specification)
        dataset_id = str(original.dataset_id)
        previous = set(session.scalars(select(ExperimentPlanRecord.id)).all())
    begin = sessions.begin

    @contextmanager
    def lose_ack():
        with begin() as session:
            yield session
            planned = any(
                isinstance(row, ExperimentPlanRecord) for row in session.identity_map.values()
            )
        if planned:
            raise ConnectionError("OWNED_PLAN_COMMIT_ACK_LOST")

    with monkeypatch.context() as scope:
        scope.setattr(sessions, "begin", lose_ack)
        response = client.post(
            base + "/experiments/plans",
            headers=admin,
            json={"dataset_id": dataset_id, "specification": specification.model_dump(mode="json")},
        )
    assert response.status_code == 500
    with sessions() as session:
        committed = session.scalars(
            select(ExperimentPlanRecord).where(ExperimentPlanRecord.project_id == UUID(tenant[0]))
        ).all()
        late = [row for row in committed if row.id not in previous]
        assert len(late) == 1
        restored = load_plan(store, UUID(tenant[0]), late[0].specification)
        assert restored == specification
        assert restored.episode_execution.semantic_judging.criteria[0].judge_rubric.startswith(
            "PRIVATE_RUBRIC_CANARY"
        )
