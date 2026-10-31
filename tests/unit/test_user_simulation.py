import json
from decimal import Decimal

import httpx
import httpx2
import pytest
from support_native_agent import native_executor, response_body
from support_reactive_users import native_text, reactive_plan
from test_episode_runner import episode_plan

from cutback.core.episodes import EpisodeObservation, EpisodeScenario
from cutback.core.user_simulation import UserSimulationResponse
from cutback.evals.datasets import Case
from cutback.evals.episode_runner import execute_episode
from cutback.evals.experiments import BudgetLedger, ExperimentPlan, plan_summary, run


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


@pytest.mark.parametrize("metadata_only", [False, True])
def test_impossible_reactive_turn_budget_is_refused_before_approval(metadata_only):
    plan = reactive_plan()
    case = reactive_case()
    case["episode"]["max_turns"] = 1
    item = {"group_hash": "synthetic", "split": "test"}
    item.update({"episode_max_turns": 1} if metadata_only else {"case": case})
    with pytest.raises(ValueError, match="REACTIVE_USER_REQUIRES"):
        plan_summary(plan, {"immutable_hash": "a" * 64, "items": [item]})


@pytest.mark.parametrize("action", ["end", "followup"])
def test_original_scripted_turns_precede_simulation_and_receipts_do_not_weaken_plain_evaluation(
    monkeypatch, action
):
    from cutback.evals.episode_metrics import assess_episode

    calls = []

    def handle(request):
        value = json.loads(request.content)
        calls.append(value)
        if value.get("text", {}).get("format", {}).get("name") == "cutback_reactive_user":
            observed = json.loads(value["input"])["observed_conversation"]
            assert [e["text"] for e in observed if e["role"] == "user"][
                -1
            ] == "Original scripted turn"
            return native_text(
                json.dumps(
                    {"action": action, "message": "React now" if action == "followup" else ""}
                )
            )
        return httpx.Response(200, json=response_body("responses.create"))

    raw = reactive_plan().model_dump(mode="json")
    raw["episode_execution"]["max_turns"] = 4
    plan = ExperimentPlan.model_validate(raw)
    case = reactive_case()
    case["episode"].update(max_turns=4, user_followups=["Original scripted turn"])
    executor = native_executor("responses.create", monkeypatch, handle)
    ledger = BudgetLedger(plan.budget)
    observed, assessment = execute_episode(plan, plan.baseline, case, ledger, executor)
    assert assessment["status"] == "passed"
    assert len(calls) == ledger.requests == (4 if action == "followup" else 3)
    assert [e.text for e in observed.events if e.kind == "user"] == [
        case["input"],
        "Original scripted turn",
        *(["React now"] if action == "followup" else []),
    ]
    assert observed.user_simulation.status == ("turn_cap" if action == "followup" else "ended")
    if action == "followup":
        assert (
            assess_episode(EpisodeScenario.model_validate(case["episode"]), observed)["status"]
            == "failed"
        )
    executor.close()


@pytest.mark.parametrize("overrun_role", ["target", "simulator"])
def test_reported_usage_priced_above_reserved_bound_stops_every_later_dispatch(
    monkeypatch, overrun_role
):
    calls = []
    breached = []

    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        simulated = body.get("text", {}).get("format", {}).get("name") == "cutback_reactive_user"
        value = response_body("responses.create")
        if simulated:
            value["output"][0]["content"][0]["text"] = json.dumps({"action": "end", "message": ""})
        if not breached and simulated == (overrun_role == "simulator"):
            value["usage"].update(input_tokens=100000, total_tokens=100010)
            breached.append(True)
        return httpx.Response(200, json=value)

    plan = reactive_plan()
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [
            {"case": reactive_case(), "group_hash": "synthetic-bounds", "split": "test"},
        ],
    }
    executor = native_executor("responses.create", monkeypatch, handle)
    result = run(plan, dataset, executor, approval_hash=plan_summary(plan, dataset)["plan_hash"])
    assert len(calls) == (1 if overrun_role == "target" else 2)
    assert result["status"] == "FAILED" and result["billing_complete"] is False
    assert Decimal(result["known_spend_subtotal_usd"]) > 0
    executor.close()


def test_shared_budget_ledger_refuses_a_second_native_dispatch_after_known_cost_overrun(
    monkeypatch,
):
    calls = []

    def handle(request):
        calls.append(1)
        value = response_body("responses.create")
        value["usage"].update(input_tokens=100000, total_tokens=100010)
        return httpx.Response(200, json=value)

    plan = reactive_plan()
    executor = native_executor("responses.create", monkeypatch, handle)
    ledger = BudgetLedger(plan.budget)
    with pytest.raises(ValueError, match="ACTUAL_USAGE_OUTSIDE_MODELED_BUDGET_BOUND"):
        ledger.call(executor, plan.baseline, "Synthetic first request")
    with pytest.raises(ValueError, match="COST_BOUND_EXCEEDED_NO_FURTHER_REQUESTS"):
        ledger.call(executor, plan.candidates[0], "Synthetic second request")
    assert calls == [1] and ledger.requests == ledger.known_cost_requests == 1
    assert ledger.spent > 0 and not ledger.billing_complete
    executor.close()
