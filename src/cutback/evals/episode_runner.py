"""Bounded native conversation evaluation against declared synthetic fixtures."""

import json
import math
from collections import Counter
from decimal import Decimal

from cutback.core.agent_turns import AgentMessage, AgentToolDefinition, AgentTurnFrame
from cutback.core.episodes import (
    EpisodeObservation,
    EpisodeScenario,
    EpisodeUser,
    bounded_episode_value,
)
from cutback.evals.episode_fixtures import FixtureRunner
from cutback.evals.episode_metrics import assess_episode


def selected_items(plan, dataset):
    items = [item for item in dataset["items"] if item["split"] == plan.split]
    return items[: plan.sample_limit] if plan.sample_limit else items


def effective_scenario(plan, case):
    scenario = EpisodeScenario.model_validate(case.get("episode"))
    return scenario.model_copy(
        update={
            "max_turns": min(scenario.max_turns, plan.episode_execution.max_turns),
            "max_tool_calls": min(scenario.max_tool_calls, plan.episode_execution.max_tool_calls),
        }
    )


def episode_forecast(plan, dataset):
    from cutback.evals.experiments import upper_cost

    turns = 0
    for item in selected_items(plan, dataset):
        if "case" in item:
            bound = effective_scenario(plan, item["case"]).max_turns
        else:
            bound = item.get("episode_max_turns")
            if type(bound) is not int or not 1 <= bound <= 32:
                raise ValueError("TYPED_EPISODE_CASES_REQUIRED")
            bound = min(bound, plan.episode_execution.max_turns)
        turns += bound
    models = [plan.baseline, *plan.candidates]
    return (
        turns * len(models) * plan.repeats,
        turns * plan.repeats * sum((upper_cost(m) for m in models), Decimal(0)),
    )


def execute_episode(plan, model, case, ledger, executor, *, check_authority=None):
    scenario = effective_scenario(plan, case)
    fixtures = FixtureRunner(scenario)
    events = [EpisodeUser(text=case["input"])]
    messages = [AgentMessage(role="user", text=case["input"])]
    tools = [AgentToolDefinition(**tool) for tool in fixtures.visible_tools()]
    followups = iter(scenario.user_followups)
    outcome, answer, citations = "turn_limit", None, ()
    seen = set()
    for _ in range(scenario.max_turns):
        if ledger.remaining_seconds() <= 0:
            outcome = "timeout"
            break
        try:
            if check_authority:
                check_authority()
            frame = AgentTurnFrame(
                surface="messages.create"
                if model.provider == "anthropic"
                else plan.episode_execution.openai_surface,
                system=(model.instruction + "\n\n" if model.instruction else "") + scenario.system,
                messages=messages,
                tools=tools,
            )
            target = model.with_agent_turn(frame)
            if hasattr(executor, "prepare"):
                executor.prepare(model.provider)
            result = ledger.call(executor, target, "")
            if check_authority:
                check_authority()
        except (TimeoutError, ConnectionError):
            outcome = "outage"
            break
        except ValueError as exc:
            if ledger.receipt_uncertain:
                raise ValueError("CALL_RECEIPT_WRITE_UNCERTAIN_NO_REQUEST_RETRY") from None
            outcome = (
                "budget_limit"
                if str(exc) == "BUDGET_EXHAUSTED"
                else "timeout"
                if str(exc) == "BUDGET_WALL_EXPIRED_BEFORE_REQUEST"
                else "unknown_usage"
                if str(exc) == "ACTUAL_USAGE_OUTSIDE_MODELED_BUDGET_BOUND"
                else "policy_denied"
            )
            break
        except Exception as exc:
            outcome = (
                "outage"
                if type(exc).__module__.split(".")[0] in {"openai", "anthropic"}
                else "error"
            )
            break
        event = result.agent_turn
        if (
            event is None
            or len(event.tool_calls) > 1
            or any(call.call_id in seen for call in event.tool_calls)
        ):
            outcome = "error"
            break
        try:
            bounded_episode_value(
                [e.model_dump(mode="json") for e in [*events, event]], max_bytes=192000
            )
        except ValueError:
            outcome = "content_limit"
            break
        events.append(event)
        messages.append(
            AgentMessage(role="assistant", text=result.output, tool_calls=event.tool_calls)
        )
        if event.finish == "incomplete":
            outcome = "incomplete"
            break
        if event.finish in {"completed", "refused"}:
            next_user = next(followups, None) if event.finish == "completed" else None
            if next_user is None:
                outcome, answer, citations = event.finish, event.text, result.agent_citations
                break
            events.append(EpisodeUser(text=next_user))
            messages.append(AgentMessage(role="user", text=next_user))
            continue
        for call in event.tool_calls:
            seen.add(call.call_id)
            try:
                if check_authority:
                    check_authority()
                executed = fixtures.execute(call)
            except Exception:
                outcome = "policy_denied"
                break
            events.append(executed.event)
            if executed.retrieval:
                events.append(executed.retrieval)
            messages.append(
                AgentMessage(
                    role="tool",
                    tool_call_id=call.call_id,
                    tool_error=executed.event.status != "returned",
                    text=json.dumps(executed.event.result, ensure_ascii=True, allow_nan=False),
                )
            )
            if len(seen) > scenario.max_tool_calls:
                outcome = "tool_limit"
                break
        if outcome in {"policy_denied", "tool_limit"}:
            break
    try:
        observation = EpisodeObservation.model_validate(
            {
                "events": events,
                "outcome": outcome,
                "final_answer": answer,
                "citations": list(citations),
                "final_state": fixtures.state,
            }
        )
    except ValueError:
        # Content bounds cannot turn a partial conversation into a successful task.
        observation = EpisodeObservation(
            events=[events[0]],
            outcome="content_limit",
            truncated=True,
            final_state=scenario.initial_state,
        )
    return observation, assess_episode(scenario, observation, cutoff=plan.metric.cutoff)


def run_episodes(
    plan,
    dataset,
    executor,
    *,
    reserve=None,
    reserve_call=None,
    record_call=None,
    record_case=None,
    episode_lifecycle=None,
    check_authority=None,
):
    from cutback.evals.experiments import BudgetLedger, plan_summary

    if (
        plan.episode_execution.transport == "scripted_test_only"
        and getattr(executor, "synthetic_transport", False) is not True
    ):
        raise ValueError("SCRIPTED_EPISODE_REQUIRES_EXPLICIT_SYNTHETIC_EXECUTOR")

    ledger = BudgetLedger(
        plan.budget, reserve, plan.price_sha256, reserve_call=reserve_call, record_call=record_call
    )
    items = selected_items(plan, dataset)
    model_results = []
    try:
        for model in [plan.baseline, *plan.candidates]:
            states: Counter[str] = Counter()
            outcomes: Counter[str] = Counter()
            failures: Counter[str] = Counter()
            cost_before, requests_before, known_before = (
                ledger.spent,
                ledger.requests,
                ledger.known_cost_requests,
            )
            latencies: list[float] = []
            for repeat in range(plan.repeats):
                for item in items:
                    case = item["case"]
                    scenario = effective_scenario(plan, case)
                    if episode_lifecycle:
                        episode_lifecycle.start_episode(
                            model, case["id"], repeat, scenario.max_turns
                        )
                    observed, assessment = execute_episode(
                        plan, model, case, ledger, executor, check_authority=check_authority
                    )
                    if check_authority:
                        check_authority()
                    if episode_lifecycle:
                        episode_lifecycle.finish_episode(observed, assessment)
                    states[assessment["status"]] += 1
                    outcomes[observed.outcome] += 1
                    failures.update(
                        check["key"]
                        for check in assessment.get("checks", [])
                        if check["status"] == "failed"
                    )
                    latency = [e.duration_ms for e in observed.events if e.kind == "assistant"]
                    latencies.extend(
                        value
                        for v in latency
                        if v is not None and math.isfinite(value := float(v)) and value >= 0
                    )
                    if record_case and plan.retain_case_outputs:
                        record_case(
                            {
                                "case": case,
                                "model_key": model.key,
                                "repeat": repeat,
                                "output": observed.model_dump(mode="json"),
                                "complete": observed.outcome in {"completed", "refused"},
                                "metric_minimum": plan.metric.minimum,
                                "metric_value": assessment["score"],
                                "episode_assessment": assessment,
                                "criteria": [],
                            }
                        )
            total = len(items) * plan.repeats
            decided = states["passed"] + states["failed"]
            score = states["passed"] / total if decided == total and total else None
            cost = ledger.spent - cost_before
            requests, known = (
                ledger.requests - requests_before,
                ledger.known_cost_requests - known_before,
            )
            quality = {
                "value": score,
                "sample_n": len(items),
                "independent_group_n": len({i["group_hash"] for i in items}),
                "confidence_95": {"low": None, "high": None},
                "failures": states["failed"],
                "semantic_quality": "NOT VERIFIED",
                "evidence_tier": 1,
                "unknown": states["unknown"],
                "errors": states["error"],
                "scope": "declared_synthetic_episode_contract",
            }
            model_results.append(
                {
                    "model": model.model_dump(mode="json"),
                    "quality": quality,
                    "metric_min": score,
                    "metric_max": score,
                    "repeats": [quality],
                    "criteria": [],
                    "episodes": total,
                    "outcomes": dict(outcomes),
                    "assessment_states": dict(states),
                    "failed_checks": dict(failures),
                    "decided_coverage": decided / total if total else None,
                    "known_cost_subtotal_usd": str(cost),
                    "modeled_cost_usd": str(cost) if known == requests else None,
                    "cost_complete": known == requests,
                    "requests": requests,
                    "known_cost_requests": known,
                    "latency_ms": latencies,
                    "latency_coverage": len(latencies) / requests if requests else None,
                    "latency_scope": "native_responses_excludes_tool_and_worker_time",
                    "model_cost_per_declared_success_usd": str(cost / states["passed"])
                    if known == requests and decided == total and states["passed"]
                    else None,
                    "production_task_cost_usd": None,
                    "population_coverage": "unknown",
                    "cost_evidence": "scripted_fixture_usage"
                    if plan.episode_execution.transport == "scripted_test_only"
                    else "provider_usage_with_pinned_public_rate",
                }
            )
        return {
            "schema_version": "1.0",
            "status": "COMPLETED",
            "sample_n": len(items),
            "execution_version": plan.episode_execution.version,
            "transport_evidence": plan.episode_execution.transport,
            "plan": plan_summary(plan, dataset),
            "dataset_hash": plan.dataset_hash,
            "models": model_results,
            "judges": [],
            "recommendations": [],
            "recommendation_gate": "synthetic_episode_results_require_application_confirmation",
            "reserved_usd": str(ledger.reserved),
            "spent_modeled_usd": str(ledger.spent) if ledger.billing_complete else None,
            "known_spend_subtotal_usd": str(ledger.spent),
            "requests": ledger.requests,
            "known_cost_requests": ledger.known_cost_requests,
            "billing_complete": ledger.billing_complete,
            "production_deployed": False,
        }
    except Exception:
        return {
            "status": "FAILED",
            "code": "EPISODE_AUTHORITY_OR_EXECUTION_FAILED",
            "models": model_results,
            "recommendations": [],
            "known_spend_subtotal_usd": str(ledger.spent),
            "requests": ledger.requests,
            "billing_complete": False,
            "production_deployed": False,
        }
