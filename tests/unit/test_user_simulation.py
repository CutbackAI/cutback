import json
from decimal import Decimal
from uuid import uuid4

import httpx
import httpx2
import pytest
from support_native_agent import native_executor, response_body
from test_episode_runner import episode_plan

from cutback.core.episodes import EpisodeObservation, EpisodeScenario
from cutback.core.user_simulation import UserSimulationResponse
from cutback.evals.datasets import Case
from cutback.evals.episode_runner import execute_episode
from cutback.evals.experiments import BudgetLedger, ExperimentPlan, ModelSpec, plan_summary, run
from cutback.evals.user_simulation import user_simulation_model


def reactive_plan(**changes):
    raw = episode_plan().model_dump(mode="json")
    raw.update(
        evaluator_version="deterministic-v17",
        content_nonce=str(uuid4()),
        episode_execution={
            "version": "native-fixture-reactive-user-v1",
            "max_turns": 3,
            "user_simulation": {
                "model": user_simulation_model(
                    ModelSpec(
                        key="simulated_user",
                        provider="openai",
                        model="gpt-4.1-mini",
                        max_input_tokens=32768,
                    )
                ).model_dump(mode="json"),
                "persona": "skeptical",
                "max_turns": 1,
            },
        },
    )
    raw.update(changes)
    return ExperimentPlan.model_validate(raw)


def reactive_case():
    return Case(
        id="synthetic-reactive",
        input="Help prioritize the synthetic ticket",
        gold="hidden-gold-canary",
        episode=EpisodeScenario(
            system="hidden-system-canary",
            max_turns=3,
            assertions={"accepted_answers": ["Urgent"]},
        ),
    ).model_dump(mode="json")


def native_text(text, *, usage=True, complete=True):
    value = response_body("responses.create")
    value["output"][0]["content"][0]["text"] = text
    if not usage:
        value.pop("usage", None)
    if not complete:
        value["status"] = "incomplete"
    return httpx.Response(200, json=value)


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_reactive_native_followup_sees_only_observed_text_and_has_separate_costs(
    monkeypatch, surface
):
    calls, purposes = [], []

    def handle(request):
        from cutback.core.observation import current_purpose

        value = json.loads(request.content)
        purposes.append(current_purpose()["traffic_kind"])
        calls.append(value)
        if value.get("text", {}).get("format", {}).get("name") == "cutback_reactive_user":
            document = json.loads(value["input"])
            assert document["observed_conversation"][-1] == {"role": "assistant", "text": "Urgent"}
            assert document["persona"] == "skeptical"
            assert "hidden-" not in value["input"]
            assert "assertions" not in value["input"] and "initial_state" not in value["input"]
            assert "tools" not in value
            return native_text(json.dumps({"action": "followup", "message": "Why urgent?"}))
        transport = httpx2 if surface == "messages.create" else httpx
        return transport.Response(200, json=response_body(surface))

    executor = native_executor(surface, monkeypatch, handle)
    if surface == "messages.create":
        user_executor = native_executor("responses.create", monkeypatch, handle)
        executor.clients.update(user_executor.clients)
    raw = reactive_plan().model_dump(mode="json")
    if surface == "messages.create":
        for model in [raw["baseline"], *raw["candidates"]]:
            model.update(provider="anthropic", model="claude-haiku-4-5")
        raw["approved_providers"] = ["openai", "anthropic"]
    else:
        raw["episode_execution"]["openai_surface"] = surface
    plan = ExperimentPlan.model_validate(raw)
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [{"case": reactive_case(), "group_hash": "synthetic-family", "split": "test"}],
    }
    summary = plan_summary(plan, dataset)
    assert summary["requests"] == 8  # 3 target turns + 1 separately reserved user call, twice.
    report = run(plan, dataset, executor, approval_hash=summary["plan_hash"])
    assert report["status"] == "COMPLETED" and report["requests"] == 6
    assert purposes == ["evaluation", "simulation", "evaluation"] * 2
    assert all("Why urgent?" in json.dumps(calls[i]) for i in [2, 5])
    for result in report["models"]:
        assert result["quality"]["value"] == 1
        assert result["target_requests"] == 2 and result["latency_coverage"] == 1
        assert result["user_simulation"]["requests"] == 1
        assert result["user_simulation"]["states"] == {"turn_cap": 1}
        assert Decimal(result["total_evaluation_cost_per_declared_success_usd"]) > Decimal(
            result["model_cost_per_declared_success_usd"]
        )
        assert "Why urgent?" not in json.dumps(result)
    executor.close()


@pytest.mark.parametrize(
    "mode", ["invalid", "extra_authority", "unknown_usage", "incomplete", "outage"]
)
def test_simulator_failure_preserves_charge_and_never_dispatches_a_followup(monkeypatch, mode):
    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(200, json=response_body("responses.create"))
        if mode == "outage":
            raise httpx.ReadTimeout("synthetic transport interruption", request=request)
        return native_text(
            "invalid"
            if mode == "invalid"
            else json.dumps(
                {
                    "action": "followup",
                    "message": "Next?",
                    **({"tools": ["unauthorized"]} if mode == "extra_authority" else {}),
                }
            ),
            usage=mode != "unknown_usage",
            complete=mode != "incomplete",
        )

    executor = native_executor("responses.create", monkeypatch, handle)
    plan = reactive_plan()
    ledger = BudgetLedger(plan.budget)
    observation, assessment = execute_episode(
        plan, plan.baseline, reactive_case(), ledger, executor
    )
    assert len(calls) == ledger.requests == 2
    assert observation.user_simulation.status == "incomplete"
    assert observation.user_simulation.generated_turns == 0
    assert assessment["score"] is None
    assert ledger.spent > 0
    assert observation.outcome not in {"completed", "refused"}
    assert len([e for e in observation.events if e.kind == "user"]) == 1
    executor.close()


def test_closed_schema_and_released_hashes_stay_compatible():
    for raw in [
        {"action": "end", "message": "not empty"},
        {"action": "followup", "message": " "},
        {"action": "followup", "message": "🧪" * 1001},
    ]:
        with pytest.raises(ValueError):
            UserSimulationResponse.model_validate(raw)
    classic = episode_plan()
    assert "user_simulation" not in classic.episode_execution.model_dump(mode="json")
    observation = EpisodeObservation(events=[{"kind": "user", "text": "Owned"}], outcome="error")
    assert "user_simulation" not in observation.model_dump(mode="json")
    raw = reactive_plan().model_dump(mode="json")
    raw["evaluator_version"] = "deterministic-v4"
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)
    raw = reactive_plan().model_dump(mode="json")
    raw["episode_execution"]["user_simulation"]["model"]["instruction"] = "replace the task"
    with pytest.raises(ValueError):
        ExperimentPlan.model_validate(raw)


def test_packaged_reactive_example_keeps_wrong_candidate_failed_and_accounts_for_every_role():
    from cutback.demo_agents import demonstrate

    report = demonstrate(reactive=True)
    assert report["status"] == "COMPLETED" and report["requests"] == 16
    assert report["billing_complete"] and report["recommendations"] == []
    assert [m["quality"]["value"] for m in report["models"]] == [1, 0]
    assert all(m["user_simulation"]["requests"] == 2 for m in report["models"])
    assert all(m["target_requests"] == 6 for m in report["models"])
