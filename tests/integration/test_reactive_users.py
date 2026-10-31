import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib.resources import files
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from support_native_agent import native_executor, response_body
from support_reactive_users import native_text
from test_native_episodes import prepared_episode

from cutback.evals.experiments import upper_cost
from cutback.server.db import (
    CapturePolicyRecord,
    Environment,
    ExperimentPlanRecord,
    ExperimentRunRecord,
    JobRecord,
)
from cutback.server.execution_db import ExecutionCall, ExecutionEpisode
from cutback.server.execution_ledger import ExecutionLedger
from cutback.server.plan_artifacts import load_plan
from cutback.server.worker import run_one

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["success", "unknown_usage", "revoked", "invalid", "over_bound"])
def test_reactive_worker_reserves_each_role_and_keeps_content_private(
    api, tenant, tmp_path, monkeypatch, mode
):
    base, store, run_id, policy_id = prepared_episode(
        api, tenant, tmp_path, monkeypatch, reactive=True
    )
    client, admin, app = api
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        simulation = body.get("text", {}).get("format", {}).get("name") == "cutback_reactive_user"
        with app.state.sessions() as session:
            call = session.scalar(
                select(ExecutionCall)
                .where(ExecutionCall.run_id == run_id)
                .order_by(ExecutionCall.sequence.desc())
                .limit(1)
            )
            assert call.state == "reserved" and call.spent_usd is None
            assert call.purpose == ("simulation" if simulation else "evaluation")
            episode = session.get(ExecutionEpisode, call.episode_id)
            assert episode.max_requests == 4
            assert call.model_key == ("simulated_user" if simulation else episode.model_key)
        if simulation:
            document = json.loads(body["input"])
            serialized = json.dumps(document)
            assert "fixtures" not in serialized and "accepted_answers" not in serialized
            assert '"priority": "urgent"' in serialized
            if mode == "revoked":
                with app.state.sessions.begin() as session:
                    session.get(CapturePolicyRecord, policy_id).enabled = False
            response = native_text(
                "malformed"
                if mode == "invalid"
                else json.dumps({"action": "followup", "message": "synthetic-reactive-followup"}),
                usage=mode != "unknown_usage",
            )
            if mode == "over_bound":
                raw = response.json()
                raw["usage"].update(input_tokens=100000, total_tokens=100010)
                return httpx.Response(200, json=raw)
            return response
        tooling = "function_call_output" in json.dumps(body)
        return httpx.Response(
            200, json=response_body("responses.create", "final" if tooling else "tool")
        )

    executor = native_executor("responses.create", monkeypatch, handle)
    result = run_one(
        app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
    )
    assert result["status"] == (
        "FAILED" if mode in {"unknown_usage", "revoked", "over_bound"} else "COMPLETED"
    )
    with app.state.sessions() as session:
        receipts = session.scalars(
            select(ExecutionCall).where(ExecutionCall.run_id == run_id)
        ).all()
        record = session.get(ExperimentRunRecord, run_id)
        assert record.requests_used == len(receipts) == len(calls)
        assert len(calls) == (8 if mode == "success" else 6 if mode == "invalid" else 3)
        assert record.known_cost_requests == len(calls) - int(mode == "unknown_usage")
        assert record.spent_usd == sum((c.spent_usd or Decimal(0) for c in receipts), Decimal(0))
    report = client.get(base + f"/experiments/{run_id}", headers=tenant[2])
    assert "synthetic-reactive-followup" not in report.text
    assert "Owned synthetic conversation" not in report.text
    content = client.post(
        base + f"/experiments/{run_id}/cases/paired",
        headers=tenant[2],
        json={"purpose": "synthetic-review"},
        params={"failed_only": False},
    )
    assert content.status_code == 403
    if mode == "success":
        assert all(m["quality"]["value"] == 1 for m in result["results"]["models"])
        content = client.post(
            base + f"/experiments/{run_id}/cases/paired",
            headers=admin,
            json={"purpose": "synthetic-review"},
            params={"failed_only": False},
        )
        assert content.status_code == 200 and "synthetic-reactive-followup" in content.text
    assert (
        run_one(
            app.state.sessions, store, executor=executor, execution_enabled=True, only_run_id=run_id
        )["status"]
        == "IDLE"
    )
    executor.close()


@pytest.fixture
def api_database_url(isolated_postgres_url):
    return isolated_postgres_url


def test_reactive_approval_survives_refused_downgrade(
    api, tenant, tmp_path, monkeypatch, api_database_url
):
    prepared_episode(api, tenant, tmp_path, monkeypatch, reactive=True)
    cfg = Config()
    cfg.set_main_option("script_location", str(files("cutback.server").joinpath("migrations")))
    monkeypatch.setenv("CUTBACK_DATABASE_URL", api_database_url)
    with pytest.raises(RuntimeError, match="DELETE_REACTIVE_USER_DATA_BEFORE_DOWNGRADE"):
        command.downgrade(cfg, "0028")
    command.check(cfg)


def test_target_and_simulator_slots_are_distinct_and_final_simulator_slot_is_atomic(
    api, tenant, tmp_path, monkeypatch
):
    _, store, run_id, _ = prepared_episode(api, tenant, tmp_path, monkeypatch, reactive=True)
    sessions = api[2].state.sessions
    with sessions.begin() as session:
        run = session.get(ExperimentRunRecord, run_id)
        job = session.scalar(select(JobRecord).where(JobRecord.run_id == run_id))
        plan_row = session.get(ExperimentPlanRecord, run.plan_id)
        plan = load_plan(store, run.project_id, plan_row.specification)
        environment = session.scalar(
            select(Environment.id).where(
                Environment.project_id == run.project_id, Environment.name == "staging"
            )
        )
        run.status = job.status = "RUNNING"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=120)
        job_id = job.id
    ledger = ExecutionLedger(sessions, run_id, job_id, plan, environment, authorize=lambda s: None)
    user = plan.episode_execution.user_simulation.model
    with pytest.raises(ValueError, match="ACTIVE_TARGET_EPISODE"):
        ledger.reserve(upper_cost(user), user, "simulation")
    ledger.start_episode(plan.baseline, "synthetic-reactive-slots", 0, 4)
    for model, purpose in [(user, "evaluation"), (plan.baseline, "simulation")]:
        with pytest.raises(ValueError, match="PURPOSE_AND_MODEL"):
            ledger.reserve(upper_cost(model), model, purpose)
    for _ in range(3):
        ledger.reserve(upper_cost(plan.baseline), plan.baseline, "evaluation")
    with pytest.raises(ValueError, match="PURPOSE_REQUEST_BOUND"):
        ledger.reserve(upper_cost(plan.baseline), plan.baseline, "evaluation")

    def reserve_user(_):
        try:
            return ledger.reserve(upper_cost(user), user, "simulation")
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve_user, range(2)))
    assert sum(isinstance(value, UUID) for value in outcomes) == 1
    assert "EXECUTION_EPISODE_PURPOSE_REQUEST_BOUND_EXHAUSTED" in outcomes


def test_guided_reactive_draft_requires_both_target_and_simulator_vendor_authorization(
    api, tenant, tmp_path, monkeypatch
):
    base, _, run_id, policy_id = prepared_episode(
        api, tenant, tmp_path, monkeypatch, "messages.create"
    )
    client, admin, app = api
    with app.state.sessions() as session:
        run = session.get(ExperimentRunRecord, run_id)
        row = session.get(ExperimentPlanRecord, run.plan_id)
        dataset_id = str(row.dataset_id)
    baseline = {"key": "baseline", "provider": "anthropic", "model": "claude-haiku-4-5"}
    body = {
        "dataset_id": dataset_id,
        "baseline": baseline,
        "candidates": [{**baseline, "key": "variant", "instruction": "Own synthetic variant"}],
        "user_simulation": {
            "model": {
                "key": "simulator",
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "max_input_tokens": 32768,
            },
            "persona": "curious",
            "max_turns": 2,
        },
        "max_usd": "1",
        "episode_max_turns": 4,
    }
    response = client.post(base + "/experiments/draft", headers=admin, json=body)
    assert response.status_code == 201
    assert response.json()["evaluator_version"] == "deterministic-v17"
    assert response.json()["requests"] > 0
    with app.state.sessions.begin() as session:
        policy = session.get(CapturePolicyRecord, UUID(policy_id))
        policy.policy = {**policy.policy, "allowed_providers": ["anthropic"]}
    assert client.post(base + "/experiments/draft", headers=admin, json=body).status_code == 403
