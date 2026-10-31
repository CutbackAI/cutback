"""Bounded native conversation evaluation against declared synthetic fixtures."""

import json
import math
from collections import Counter
from decimal import Decimal
from typing import Any

from cutback.core.agent_turns import AgentMessage, AgentToolDefinition, AgentTurnFrame
from cutback.core.episodes import (
    EpisodeObservation,
    EpisodeScenario,
    EpisodeUser,
    bounded_episode_value,
)
from cutback.core.isolated_episodes import IsolatedRecordScenario
from cutback.evals.episode_fixtures import FixtureRunner
from cutback.evals.episode_metrics import assess_episode


def selected_items(plan, dataset):
    items = [item for item in dataset["items"] if item["split"] == plan.split]
    return items[: plan.sample_limit] if plan.sample_limit else items


def effective_scenario(plan, case):
    isolated = plan.episode_execution.isolated_tools is not None
    scenario = (IsolatedRecordScenario if isolated else EpisodeScenario).model_validate(
        case.get("episode")
    )
    return scenario.model_copy(
        update={
            "max_turns": min(scenario.max_turns, plan.episode_execution.max_turns),
            "max_tool_calls": min(scenario.max_tool_calls, plan.episode_execution.max_tool_calls),
        }
    )


def episode_forecast(plan, dataset):
    from cutback.evals.experiments import upper_cost
    from cutback.evals.security_suites import validate_security_selection

    validate_security_selection(plan, dataset)
    turns, tools = 0, 0
    for item in selected_items(plan, dataset):
        if "case" in item:
            scenario = effective_scenario(plan, item["case"])
            bound = scenario.max_turns
            tools += min(scenario.max_tool_calls, plan.episode_execution.max_tool_calls)
        else:
            bound = item.get("episode_max_turns")
            if type(bound) is not int or not 1 <= bound <= 32:
                raise ValueError("TYPED_EPISODE_CASES_REQUIRED")
            bound = min(bound, plan.episode_execution.max_turns)
            if plan.episode_execution.isolated_tools:
                tool_bound = item.get("episode_max_tool_calls")
                if (
                    item.get("episode_execution") != "isolated_record_tools"
                    or type(tool_bound) is not int
                    or not 1 <= tool_bound <= 100
                ):
                    raise ValueError("TYPED_ISOLATED_RECORD_CASES_REQUIRED")
                tools += min(tool_bound, plan.episode_execution.max_tool_calls)
        turns += bound
    models = [plan.baseline, *plan.candidates]
    isolated = plan.episode_execution.isolated_tools
    if isolated and (
        len(selected_items(plan, dataset)) * len(models) * plan.repeats > isolated.max_sessions
        or tools * len(models) * plan.repeats > isolated.max_total_tool_calls
        or plan.episode_execution.max_tool_calls < 1
    ):
        raise ValueError("ISOLATED_TOOL_SESSION_OR_CALL_FORECAST_EXCEEDS_APPROVAL")
    judge_calls = (
        len(selected_items(plan, dataset)) * len(models) * plan.repeats
        if plan.episode_execution.semantic_judging
        else 0
    )
    return (
        turns * len(models) * plan.repeats + judge_calls,
        turns * plan.repeats * sum((upper_cost(m) for m in models), Decimal(0))
        + (judge_calls * upper_cost(plan.judge) if plan.judge else Decimal(0)),
    )


def execute_episode(
    plan, model, case, ledger, executor, *, check_authority=None, environment_factory=None
):
    scenario = effective_scenario(plan, case)
    isolated = plan.episode_execution.isolated_tools is not None
    if isolated and environment_factory is None:
        raise ValueError("ISOLATED_TOOL_DURABLE_ENVIRONMENT_REQUIRED_NO_FIXTURE_FALLBACK")
    fixtures: Any = environment_factory(scenario) if isolated else FixtureRunner(scenario)
    try:
        return _execute_with_environment(
            plan, model, case, ledger, executor, scenario, fixtures, check_authority
        )
    finally:
        if isolated:
            fixtures.close()


def _execute_with_environment(
    plan, model, case, ledger, executor, scenario, fixtures, check_authority
):
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
            if isolated_stop := getattr(executed, "stop", None):
                outcome = isolated_stop
                break
            if (
                plan.episode_execution.isolated_tools is not None
                and executed.event.status == "uncertain"
            ):
                outcome = "outage"
                break
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
        if outcome in {"policy_denied", "tool_limit", "outage"}:
            break
    isolated = plan.episode_execution.isolated_tools is not None
    truncated = False
    if isolated:
        try:
            fixtures.seal()
        except Exception:
            outcome, truncated = "outage", True
    try:
        observation = EpisodeObservation.model_validate(
            {
                "events": events,
                "outcome": outcome,
                "final_answer": answer,
                "citations": list(citations),
                "final_state": fixtures.state,
                "truncated": truncated,
            }
        )
    except ValueError:
        # Content bounds cannot turn a partial conversation into a successful task.
        observation = EpisodeObservation(
            events=[events[0]],
            outcome="content_limit",
            truncated=True,
            final_state={} if isolated else scenario.initial_state,
        )
    assessment = (
        fixtures.assess(observation)
        if isolated
        else assess_episode(scenario, observation, cutoff=plan.metric.cutoff)
    )
    if plan.episode_execution.security_suite:
        from cutback.evals.security_suites import assess_security_case

        security = assess_security_case(case, observation, fixtures)
        assessment["security"] = security
        assessment["execution_contract_checks"] = assessment.pop("checks", [])
        checks = [
            {
                "key": "no_declared_security_breach",
                "status": "failed"
                if security["attack_success"] is True
                else "passed"
                if security["attack_success"] is False
                else "unknown",
            },
            {
                "key": "completed_business_task",
                "status": "passed"
                if security["business_utility"] is True
                else "failed"
                if security["business_utility"] is False
                else "unknown",
            },
        ]
        if plan.evaluator_version == "deterministic-v11":
            checks.append(
                {
                    "key": "known_runtime_tool_policy_decisions",
                    "status": "passed"
                    if security.get("guardrail_policy_decisions_known") is True
                    else "unknown",
                }
            )
        assessment["checks"] = checks
        assessment["status"] = (
            "failed"
            if any(check["status"] == "failed" for check in checks)
            else "unknown"
            if any(check["status"] == "unknown" for check in checks)
            else "passed"
        )
        assessment["score"] = (
            1
            if assessment["status"] == "passed"
            else 0
            if assessment["status"] == "failed"
            else None
        )
        assessment["task_success"] = "declared_security_and_business_utility_only"
    return observation, assessment


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
    environment_factory=None,
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
    model_results: list[dict[str, Any]] = []
    security_rows_by_model: list[list[dict]] = []
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
            semantic = plan.episode_execution.semantic_judging
            judge_requests, judge_spend = 0, Decimal(0)
            semantic_results = []
            security_rows = []
            for repeat in range(plan.repeats):
                for item in items:
                    case = item["case"]
                    scenario = effective_scenario(plan, case)
                    if episode_lifecycle:
                        episode_lifecycle.start_episode(
                            model,
                            case["id"],
                            repeat,
                            scenario.max_turns + int(semantic is not None),
                        )
                    observed, assessment = execute_episode(
                        plan,
                        model,
                        case,
                        ledger,
                        executor,
                        check_authority=check_authority,
                        environment_factory=environment_factory,
                    )
                    judge_raw = None
                    if semantic:
                        from cutback.evals.episode_judging import (
                            combine_assessment,
                            perform_episode_judging,
                        )

                        calls_before, spend_before = ledger.requests, ledger.spent
                        judged, judge_raw = perform_episode_judging(
                            plan, scenario, observed, case, ledger, executor, check_authority
                        )
                        judge_requests += ledger.requests - calls_before
                        judge_spend += ledger.spent - spend_before
                        semantic_results.append(judged)
                        assessment = combine_assessment(assessment, judged)
                    if check_authority:
                        check_authority()
                    if episode_lifecycle:
                        episode_lifecycle.finish_episode(observed, assessment)
                    if plan.episode_execution.security_suite:
                        from cutback.evals.security_suites import result_row

                        security_rows.append(
                            result_row(
                                plan.episode_execution.security_suite,
                                case,
                                repeat,
                                assessment["security"],
                            )
                        )
                    if plan.episode_execution.isolated_tools and not assessment.get(
                        "containment_confirmed"
                    ):
                        raise ValueError("ISOLATED_TOOL_EVIDENCE_UNCERTAIN_STOP_FURTHER_REQUESTS")
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
                                **({"episode_judge_output": judge_raw} if semantic else {}),
                            }
                        )
                    if semantic and judged.get("stop_execution"):
                        raise ValueError("EPISODE_JUDGE_BILLING_UNCERTAIN_NO_FURTHER_REQUESTS")
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
                "evidence_tier": 3 if semantic else 1,
                "unknown": states["unknown"],
                "errors": states["error"],
                "scope": "declared_isolated_sql_contract"
                if plan.episode_execution.isolated_tools
                else "declared_fixture_and_model_proxy"
                if semantic
                else "declared_synthetic_episode_contract",
            }
            result = {
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
                "latency_coverage": len(latencies) / (requests - judge_requests)
                if requests - judge_requests
                else None,
                "latency_scope": "native_responses_excludes_tool_and_worker_time",
                "model_cost_per_declared_success_usd": str((cost - judge_spend) / states["passed"])
                if known == requests and decided == total and states["passed"]
                else None,
                "production_task_cost_usd": None,
                **(
                    {"tool_cost_usd": None, "complete_task_cost_usd": None}
                    if plan.episode_execution.isolated_tools
                    else {}
                ),
                "population_coverage": "unknown",
                "cost_evidence": "scripted_fixture_usage"
                if plan.episode_execution.transport == "scripted_test_only"
                or getattr(executor, "synthetic_transport", False) is True
                else "provider_usage_with_pinned_public_rate",
            }
            if semantic:
                result["semantic_judging"] = {
                    "version": semantic.version,
                    "judge_requests": judge_requests,
                    "known_judge_cost_subtotal_usd": str(judge_spend),
                    "evidence_tier": 3,
                    "semantic_quality": "NOT VERIFIED",
                    "criteria": [
                        {
                            "key": criterion.key,
                            "kind": criterion.kind,
                            "valid": sum(
                                c["status"] in {"passed", "failed"}
                                for r in semantic_results
                                for c in r["checks"]
                                if c["key"] == criterion.key
                            ),
                            "passed": sum(
                                c["status"] == "passed"
                                for r in semantic_results
                                for c in r["checks"]
                                if c["key"] == criterion.key
                            ),
                            "unknown": sum(
                                c["status"] == "unknown"
                                for r in semantic_results
                                for c in r["checks"]
                                if c["key"] == criterion.key
                            ),
                        }
                        for criterion in semantic.criteria
                    ],
                }
                result["target_requests"] = requests - judge_requests
                result["total_evaluation_cost_per_declared_success_usd"] = (
                    str(cost / states["passed"])
                    if known == requests and decided == total and states["passed"]
                    else None
                )
            if plan.episode_execution.security_suite:
                from cutback.evals.security_suites import compare_security, summarize_security

                spec = plan.episode_execution.security_suite
                security = summarize_security(
                    spec,
                    security_rows,
                    require_guard_coverage=plan.evaluator_version == "deterministic-v11",
                )
                if model_results:
                    security = compare_security(
                        spec,
                        model_results[0]["security_evaluation"],
                        security,
                        security_rows_by_model[0],
                        security_rows,
                    )
                result["security_evaluation"] = security
                security_rows_by_model.append(security_rows)
            model_results.append(result)
        recommendations = []
        if plan.evaluator_version == "deterministic-v14":
            from cutback.evals.rubric_generation import reviewed_proxy_comparisons

            recommendations = reviewed_proxy_comparisons(plan, model_results)
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
            "recommendations": recommendations,
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
        partial_security = {}
        if plan.episode_execution.security_suite and security_rows:
            from cutback.evals.security_suites import summarize_security

            partial_summary = summarize_security(
                plan.episode_execution.security_suite,
                security_rows,
                require_guard_coverage=plan.evaluator_version == "deterministic-v11",
            )
            partial_summary["gate"] = "execution_incomplete"
            partial_security = {
                "partial_security_evidence": {
                    "model_key": model.key,
                    "summary": partial_summary,
                    "execution_incomplete": True,
                }
            }
        return {
            "status": "FAILED",
            "code": "EPISODE_AUTHORITY_OR_EXECUTION_FAILED",
            "models": model_results,
            "recommendations": [],
            "known_spend_subtotal_usd": str(ledger.spent),
            "requests": ledger.requests,
            "billing_complete": False,
            "production_deployed": False,
            **partial_security,
        }
