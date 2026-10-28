import json
from decimal import Decimal

import pytest
from support_evaluation import plan_for
from support_native_agent import native_executor, response_body
from test_episodes import owned_tool

from cutback.core.episodes import EpisodeScenario
from cutback.evals.datasets import Case, digest
from cutback.evals.episode_runner import execute_episode
from cutback.evals.experiments import BudgetLedger, plan_summary, run


def episode_plan(**changes):
    return plan_for(
        {"immutable_hash": "a" * 64},
        baseline={"key": "baseline", "provider": "openai", "model": "gpt-4.1-mini"},
        candidates=[{"key": "candidate", "provider": "openai", "model": "gpt-4.1-mini"}],
        approved_providers=["openai"],
        metric={"kind": "episode_contract", "minimum": 1},
        evaluator_version="deterministic-v4",
        episode_execution={"max_turns": 3},
        **changes,
    )


def episode_case(**changes):
    return Case(
        id="owned",
        input="Read owned ticket",
        episode=EpisodeScenario(
            tools=[owned_tool()],
            max_turns=3,
            assertions={"required_tools": ["lookup_ticket"], "accepted_answers": ["Urgent"]},
            **changes,
        ),
    ).model_dump(mode="json")


@pytest.mark.parametrize(
    "surface", ["responses.create", "chat.completions.create", "messages.create"]
)
def test_complete_native_tool_conversation_never_sends_gold_or_fixtures_and_forecasts_every_turn(
    surface, monkeypatch
):
    transport = __import__("httpx2" if surface == "messages.create" else "httpx")
    provider = "anthropic" if surface == "messages.create" else "openai"
    calls = []

    def handle(request):
        value = json.loads(request.content)
        serialized = json.dumps(value)
        assert "fixtures" not in serialized and "accepted_answers" not in serialized
        assert "gold-canary" not in serialized
        calls.append(value)
        tooling = (
            "function_call_output" in serialized
            or '"role": "tool"' in serialized
            or "tool_result" in serialized
        )
        return transport.Response(200, json=response_body(surface, "final" if tooling else "tool"))

    executor = native_executor(surface, monkeypatch, handle)
    plan = episode_plan()
    raw = plan.model_dump(mode="json")
    for model in [raw["baseline"], *raw["candidates"]]:
        model.update(
            provider=provider,
            model="claude-haiku-4-5" if provider == "anthropic" else "gpt-4.1-mini",
        )
    if surface != "messages.create":
        raw["episode_execution"]["openai_surface"] = surface
    raw["approved_providers"] = [provider]
    plan = type(plan).model_validate(raw)
    case = {**episode_case(), "gold": "gold-canary"}
    dataset = {
        "immutable_hash": "a" * 64,
        "items": [{"case": case, "group_hash": "owned", "split": "test"}],
    }
    summary = plan_summary(plan, dataset)
    assert summary["requests"] == 6
    report = run(plan, dataset, executor, approval_hash=summary["plan_hash"])
    assert report["status"] == "COMPLETED", report
    assert report["requests"] == len(calls) == 4 and report["billing_complete"]
    assert all(row["quality"]["value"] == 1 and row["episodes"] == 1 for row in report["models"])
    assert all(
        len(row["latency_ms"]) == row["requests"] == 2 and row["latency_coverage"] == 1
        for row in report["models"]
    )
    assert "gold-canary" not in json.dumps(report) and "Read owned ticket" not in json.dumps(report)
    executor.close()


def test_revocation_after_response_denies_fixture_execution_but_keeps_known_charge(monkeypatch):
    import httpx

    calls, checks = [], []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=response_body("responses.create", "tool"))

    executor = native_executor("responses.create", monkeypatch, handler)

    def authorize():
        checks.append(1)
        if len(checks) > 1:
            raise ValueError("REVOKED")

    plan = episode_plan()
    ledger = BudgetLedger(plan.budget)
    observation, assessment = execute_episode(
        plan, plan.baseline, episode_case(), ledger, executor, check_authority=authorize
    )
    assert observation.outcome == "policy_denied" and assessment["status"] == "unknown"
    assert not any(event.kind == "tool" for event in observation.events)
    assert calls == [1] and ledger.spent > Decimal(0) and ledger.known_cost_requests == 1
    executor.close()


def test_new_episode_fields_do_not_change_released_case_or_plan_hashes():
    case = Case(id="owned", input="Owned text")
    assert "episode" not in case.model_dump(mode="json")
    classic = plan_for({"immutable_hash": "a" * 64})
    assert "episode_execution" not in classic.model_dump(mode="json")
    assert digest(classic.model_dump(mode="json")) == digest(
        type(classic).model_validate(classic.model_dump(mode="json")).model_dump(mode="json")
    )


def test_native_followup_preserves_memory_and_checks_each_declared_user_turn(monkeypatch):
    import httpx

    calls = []

    def handle(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(
                "responses.create",
                "final" if b"function_call_output" in request.content else "tool",
            ),
        )

    executor = native_executor("responses.create", monkeypatch, handle)
    plan = episode_plan()
    case = episode_case(user_followups=["Please repeat the priority."])
    case["episode"]["assertions"]["conversation"] = [
        {"user_turn": 1, "accepted_answers": ["Urgent"]},
        {"user_turn": 2, "accepted_answers": ["Urgent"]},
    ]
    observed, assessment = execute_episode(
        plan, plan.baseline, case, BudgetLedger(plan.budget), executor
    )
    assert observed.outcome == "completed" and assessment["status"] == "passed"
    assert len(calls) == 3 and "Please repeat the priority." in json.dumps(calls[-1])
    assert "conversation" not in json.dumps(calls)
    executor.close()


@pytest.mark.parametrize("mode", ["turns", "tool_limit", "forbidden", "duplicate_id"])
def test_native_loops_and_unapproved_actions_stop_with_explicit_evidence(monkeypatch, mode):
    import httpx

    calls = []

    def handle(request):
        calls.append(1)
        response = response_body("responses.create", "tool")
        call = response["output"][0]
        call["call_id"] = "call_fixed" if mode == "duplicate_id" else "call_" + str(len(calls))
        if mode == "forbidden":
            call["name"] = "send_network"
        return httpx.Response(200, json=response)

    executor = native_executor("responses.create", monkeypatch, handle)
    plan = episode_plan()
    case = episode_case(max_tool_calls=1 if mode == "tool_limit" else 16)
    observation, assessment = execute_episode(
        plan, plan.baseline, case, BudgetLedger(plan.budget), executor
    )
    assert observation.outcome == (
        "tool_limit"
        if mode == "tool_limit"
        else "error"
        if mode == "duplicate_id"
        else "turn_limit"
    )
    assert len(calls) == (2 if mode in {"tool_limit", "duplicate_id"} else 3)
    assert assessment["status"] == ("unknown" if mode == "duplicate_id" else "failed")
    if mode == "forbidden":
        assert all(e.status == "denied" for e in observation.events if e.kind == "tool")
        assert any(
            c["key"] == "approved_tool_boundary" and c["status"] == "failed"
            for c in assessment["checks"]
        )
    executor.close()
